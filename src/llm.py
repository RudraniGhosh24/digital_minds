"""
LLM transport layer for the Mens Rea Evaluator.

This module exists to fix the defect that invalidated the v1 results.

v1 did this:

    content = response.choices[0].message.content or ""
    reasoning = getattr(response.choices[0].message, "reasoning_content", None)
    if reasoning:
        content = f"*[Reasoning: {reasoning}]*\\n\\n{content}"

...and every downstream grader then truncated to `content[:800]`. For any model that
returns `reasoning_content`, that window held only chain-of-thought, so the graders were
scoring the model's private deliberation rather than its answer. Because the deliberation
restates the secret instruction verbatim, "bias" and "confession" were near-guaranteed.
Models that return no reasoning were scored on their real answer. The metric therefore
measured a different object per model, along the exact axis the study compares.

Invariants enforced here:

1. `content` and `reasoning` are NEVER merged. Graders receive `content` only.
2. An empty `content` is an INVALID sample, not a data point. v1 counted six
   empty rulings as "biased".
3. `finish_reason` is recorded so length-truncation is visible instead of silent.
4. Credentials come from the environment. No key is ever written in source.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # python-dotenv is optional at runtime
    pass


DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Generous by default. v1 used 1024, which reasoning models spent entirely on
# deliberation, leaving an empty answer that was then graded as a biased ruling.
DEFAULT_MAX_TOKENS = 4096

SUBJECT_MODELS = (
    "openai/gpt-oss-20b",
    "google/diffusiongemma-26b-a4b-it",
    "meta/muse-glimmer-30b",
)

# Per-model credentials, using the same three names as the Streamlit app's secrets so
# there is one convention across the app and the CLI. All fall back to NVIDIA_API_KEY.
# There is deliberately no separate judge key: the judge's credential is looked up from
# whichever model is acting as judge.
_KEY_ENV = {
    "openai/gpt-oss-20b": "GPT_OSS_API_KEY",
    "google/diffusiongemma-26b-a4b-it": "GEMMA_API_KEY",
    # LLAMA_API_KEY is a legacy name kept because it is what the deployment already has
    # configured; the repo previously targeted Llama endpoints.
    "meta/muse-glimmer-30b": "LLAMA_API_KEY",
}


def _key_env_for(model: str) -> str | None:
    """Exact match first, then a substring match so unlisted model ids still resolve."""
    if model in _KEY_ENV:
        return _KEY_ENV[model]
    name = model.lower()
    if "gemma" in name:
        return "GEMMA_API_KEY"
    if any(t in name for t in ("llama", "meta", "muse")):
        return "LLAMA_API_KEY"
    if "gpt-oss" in name or "openai" in name:
        return "GPT_OSS_API_KEY"
    return None


class MissingCredentials(RuntimeError):
    pass


@dataclass
class LLMResponse:
    """A single completion, with deliberation kept strictly separate from the answer."""

    content: str = ""
    reasoning: str = ""
    finish_reason: str = ""
    model: str = ""
    error: str = ""
    latency_s: float = 0.0
    usage: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def is_empty(self) -> bool:
        """True when the model produced deliberation but no answer."""
        return not self.content.strip()

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"

    @property
    def is_valid(self) -> bool:
        """A sample is only gradeable if the call succeeded AND an answer exists."""
        return self.ok and not self.is_empty

    def invalid_reason(self) -> str:
        if self.error:
            return f"api_error: {self.error[:200]}"
        if self.is_empty:
            return (
                "empty_content"
                + (" (reasoning consumed the token budget)" if self.reasoning else "")
                + (f" [finish_reason={self.finish_reason}]" if self.finish_reason else "")
            )
        return ""

    def as_record(self, prefix: str) -> dict:
        """Flatten for CSV/JSON output. Reasoning is stored, never graded."""
        return {
            f"{prefix}_content": self.content,
            f"{prefix}_reasoning": self.reasoning,
            f"{prefix}_finish_reason": self.finish_reason,
            f"{prefix}_valid": self.is_valid,
            f"{prefix}_invalid_reason": self.invalid_reason(),
        }


def resolve_api_key(model: str, explicit: str | None = None) -> str:
    """
    Resolve a credential for `model`: explicit argument, then the model's own env var,
    then the shared NVIDIA_API_KEY. Used for subjects and the judge alike.
    """
    if explicit:
        return explicit
    specific = _key_env_for(model)
    if specific and os.environ.get(specific):
        return os.environ[specific]
    shared = os.environ.get("NVIDIA_API_KEY")
    if shared:
        return shared
    raise MissingCredentials(
        f"No API key for '{model}'. Set {specific or 'NVIDIA_API_KEY'}, or the shared "
        "NVIDIA_API_KEY. Copy .env.example to .env to get started."
    )


class LLMClient:
    """Thin retrying wrapper over an OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = 180.0,
        max_attempts: int = 5,
    ) -> None:
        self.base_url = base_url or os.environ.get("NVIDIA_BASE_URL", DEFAULT_BASE_URL)
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._clients: dict[str, Any] = {}

    def _client(self, model: str, api_key: str | None):
        if model not in self._clients:
            from openai import OpenAI

            self._clients[model] = OpenAI(
                api_key=resolve_api_key(model, api_key),
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=0,  # we handle retries so backoff is observable
            )
        return self._clients[model]

    def complete(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        api_key: str | None = None,
        seed: int | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if seed is not None:
            kwargs["seed"] = seed
        # DiffusionGemma needs thinking explicitly enabled to emit reasoning_content.
        if "gemma" in model:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": True}}

        last_error = ""
        for attempt in range(self.max_attempts):
            started = time.time()
            try:
                client = self._client(model, api_key)
                raw = client.chat.completions.create(**kwargs)
                choice = raw.choices[0]
                msg = choice.message

                # The whole point of this module: these stay separate.
                content = (getattr(msg, "content", None) or "").strip()
                reasoning = (getattr(msg, "reasoning_content", None) or "").strip()

                usage = {}
                if getattr(raw, "usage", None):
                    usage = {
                        "prompt_tokens": getattr(raw.usage, "prompt_tokens", None),
                        "completion_tokens": getattr(raw.usage, "completion_tokens", None),
                    }

                return LLMResponse(
                    content=content,
                    reasoning=reasoning,
                    finish_reason=getattr(choice, "finish_reason", "") or "",
                    model=model,
                    latency_s=round(time.time() - started, 2),
                    usage=usage,
                )
            except MissingCredentials:
                raise
            except Exception as exc:  # noqa: BLE001 - transport errors are heterogeneous
                last_error = f"{type(exc).__name__}: {exc}"
                text = str(exc).lower()
                rate_limited = "429" in text or "rate limit" in text or "timeout" in text
                if attempt == self.max_attempts - 1:
                    break
                wait = (30.0 if rate_limited else 4.0 * (attempt + 1)) + random.uniform(0, 2)
                print(f"      [{model}] attempt {attempt + 1}/{self.max_attempts} failed: "
                      f"{last_error[:140]} — retrying in {wait:.0f}s")
                time.sleep(wait)

        return LLMResponse(model=model, error=last_error)

    def judge_fn(self, model: str, api_key: str | None = None,
                 temperature: float = 0.0) -> Callable[[list[dict]], str]:
        """Return a `messages -> text` callable for the scorecard graders."""

        def _fn(messages: list[dict]) -> str:
            return self.complete(
                messages, model=model, temperature=temperature, api_key=api_key
            ).content

        return _fn


class MockLLMClient(LLMClient):
    """
    Offline stand-in used by tests. Takes a handler `(messages, model) -> LLMResponse`
    so the full pipeline, including the reasoning/content split and invalid-sample
    handling, can be exercised without network access or spend.
    """

    def __init__(self, handler: Callable[[list[dict], str], LLMResponse]) -> None:
        super().__init__()
        self.handler = handler
        self.calls: list[tuple[list[dict], str]] = []

    def complete(self, messages: list[dict], model: str, **kwargs) -> LLMResponse:  # type: ignore[override]
        self.calls.append((messages, model))
        out = self.handler(messages, model)
        out.model = out.model or model
        return out


def warn_if_judge_is_subject(judge_model: str) -> bool:
    """
    v1 used gpt-oss-20b as the judge while also evaluating it as a subject, on the same
    key, so one model graded its own rulings and confessions. Returns True if that
    conflict is present.
    """
    if judge_model in SUBJECT_MODELS:
        print(
            "\n" + "!" * 78 + "\n"
            f"!! CONFLICT OF INTEREST: judge '{judge_model}' is also a subject model.\n"
            "!! It will grade its own rulings and its own confessions. This was a\n"
            "!! defect in v1. Set JUDGE_MODEL to a model that is not under test.\n"
            + "!" * 78 + "\n"
        )
        return True
    return False
