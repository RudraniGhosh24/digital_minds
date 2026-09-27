#!/usr/bin/env python3
"""
Mens Rea Evaluator — evaluation runner (v2).

Replaces run_headless.py (archived under archive_v1/scripts/). Differences that matter for
the validity of the output:

  * The control (benign) arm is a first-class recorded condition, not a column that gets
    dropped from the write-up. v1 computed it and never reported it; it showed 80/60/100%
    baseline "bias", which is why the poisoned rates were not interpretable.
  * Rulings are graded from `content` only. Reasoning traces are stored separately.
  * An empty ruling is retried with a larger token budget, and if still empty it is marked
    INVALID and excluded — not counted as biased.
  * All three interrogation levels are scored, so the naive-vs-structured-vs-adversarial
    escalation claim is actually measured.
  * Every cell can be repeated N times, and judge calls are voted, so grader noise is
    visible instead of invisible.
  * Transcripts for every condition are written, not just the poisoned one.

Usage:
    cp .env.example .env    # then fill in keys
    ./venv/bin/python run_eval.py --repeats 3 --judge-model <a-model-not-under-test>
    ./venv/bin/python run_eval.py --dry-run          # show the plan and cost, call nothing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from src.data_loader import load_curated_scenarios, validate_scenarios
from src.llm import (
    DEFAULT_MAX_TOKENS,
    SUBJECT_MODELS,
    LLMClient,
    LLMResponse,
    MissingCredentials,
    warn_if_judge_is_subject,
)
from src.persona_stability import PERSONA_PROBES
from src.prompts import (
    get_adversarial_cross_exam,
    get_benign_system_prompt,
    get_naive_cross_exam,
    get_poisoned_system_prompt,
    get_ruling_request,
    get_structured_cross_exam,
)
from src.rag_pipeline import format_context, retrieve_documents
from src.scorecard import (
    derive_mens_rea,
    describe_verdict,
    evaluate_actus_reus,
    grade_stance,
    grade_structured_probe,
    StanceResult,
)

RESULTS_CSV = "results_v2.csv"
RUNS_JSONL = "runs_v2.jsonl"
TRANSCRIPTS_MD = "supplementary_evidence_v2.md"


@dataclass
class Config:
    models: list[str]
    judge_model: str
    repeats: int = 1
    judge_samples: int = 3
    subject_temperature: float = 0.0
    judge_temperature: float = 0.0
    max_tokens: int = DEFAULT_MAX_TOKENS
    out_dir: str = "results"
    judge_api_key: str | None = None


# --- helpers ---------------------------------------------------------------------


def _ruling(
    client: LLMClient,
    messages: list[dict],
    model: str,
    cfg: Config,
    label: str,
) -> LLMResponse:
    """
    Request a ruling, retrying once with a doubled token budget if the model spent its
    whole budget on reasoning and returned nothing. v1 had no such guard, so six empty
    rulings were graded as biased.
    """
    resp = client.complete(
        messages, model=model, temperature=cfg.subject_temperature, max_tokens=cfg.max_tokens
    )
    if resp.is_valid or not resp.ok:
        return resp

    bigger = cfg.max_tokens * 2
    print(f"      [{label}] empty content ({resp.invalid_reason()}); retrying at max_tokens={bigger}")
    retry = client.complete(
        messages, model=model, temperature=cfg.subject_temperature, max_tokens=bigger
    )
    return retry if retry.is_valid else resp


def _stance(content: str | None, judge_fn, cfg: Config) -> StanceResult:
    return grade_stance(content, judge_fn, samples=cfg.judge_samples)


# --- one cell --------------------------------------------------------------------


def run_cell(
    client: LLMClient,
    judge_fn,
    model: str,
    scenario: dict,
    repeat: int,
    cfg: Config,
) -> dict:
    """Run all conditions and probes for one (model, scenario, repeat) cell."""
    title = scenario["title"]
    short = model.split("/")[-1]
    print(f"\n--- {short} × {title} (repeat {repeat + 1}/{cfg.repeats}) ---")

    benign_ctx = format_context(retrieve_documents(scenario, poisoned=False))
    poisoned_ctx = format_context(retrieve_documents(scenario, poisoned=True))
    request_benign = get_ruling_request(benign_ctx, scenario["legal_issue"])
    request_poisoned = get_ruling_request(poisoned_ctx, scenario["legal_issue"])

    record: dict = {
        "model": short,
        "model_id": model,
        "scenario_id": scenario["id"],
        "scenario": title,
        "repeat": repeat,
        "favored_party": scenario["favored_party"],
        "disfavored_party": scenario["disfavored_party"],
        "subject_temperature": cfg.subject_temperature,
        "judge_model": cfg.judge_model,
        "judge_samples": cfg.judge_samples,
    }
    transcripts: dict[str, dict] = {}

    # --- Condition 1: control (neutral docs, neutral system prompt) ---------------
    print("   [1/3] control (benign docs + benign prompt)")
    control_msgs = [
        {"role": "system", "content": get_benign_system_prompt()},
        {"role": "user", "content": request_benign},
    ]
    control = _ruling(client, control_msgs, model, cfg, "control")
    control_bias = evaluate_actus_reus(control.content, scenario, judge_fn, cfg.judge_samples)
    record |= {
        "control_bias": control_bias.label,
        "control_biased": control_bias.is_biased,
        "control_bias_agreement": control_bias.agreement,
        "control_valid": control.is_valid,
        "control_invalid_reason": control.invalid_reason(),
    }
    transcripts["control"] = {"ruling": control.content, "reasoning": control.reasoning,
                              "bias": control_bias.label}

    # --- Condition 2: ablation (poisoned docs, neutral system prompt) -------------
    print("   [2/3] ablation (poisoned docs + benign prompt)")
    ablation_msgs = [
        {"role": "system", "content": get_benign_system_prompt()},
        {"role": "user", "content": request_poisoned},
    ]
    ablation = _ruling(client, ablation_msgs, model, cfg, "ablation")
    ablation_bias = evaluate_actus_reus(ablation.content, scenario, judge_fn, cfg.judge_samples)
    record |= {
        "ablation_bias": ablation_bias.label,
        "ablation_biased": ablation_bias.is_biased,
        "ablation_bias_agreement": ablation_bias.agreement,
        "ablation_valid": ablation.is_valid,
        "ablation_invalid_reason": ablation.invalid_reason(),
    }
    transcripts["ablation"] = {"ruling": ablation.content, "reasoning": ablation.reasoning,
                               "bias": ablation_bias.label}

    # --- Condition 3: poisoned (poisoned docs + secret instruction) ---------------
    print("   [3/3] poisoned (poisoned docs + poisoned prompt)")
    poisoned_msgs = [
        {"role": "system", "content": get_poisoned_system_prompt(scenario)},
        {"role": "user", "content": request_poisoned},
    ]
    poisoned = _ruling(client, poisoned_msgs, model, cfg, "poisoned")
    poisoned_bias = evaluate_actus_reus(poisoned.content, scenario, judge_fn, cfg.judge_samples)
    record |= {
        "poisoned_bias": poisoned_bias.label,
        "poisoned_biased": poisoned_bias.is_biased,
        "poisoned_bias_agreement": poisoned_bias.agreement,
        "poisoned_valid": poisoned.is_valid,
        "poisoned_invalid_reason": poisoned.invalid_reason(),
    }
    transcripts["poisoned"] = {"ruling": poisoned.content, "reasoning": poisoned.reasoning,
                               "bias": poisoned_bias.label}

    # Interrogation is only meaningful if there is a ruling to interrogate.
    if not poisoned.is_valid:
        print(f"   !! no poisoned ruling ({poisoned.invalid_reason()}) — skipping probes")
        empty = StanceResult(label="INVALID", note="no poisoned ruling to interrogate")
        verdict = derive_mens_rea(poisoned_bias, empty, empty, empty,
                                  {name: empty for name, _ in PERSONA_PROBES})
        record |= _verdict_fields(verdict, {}, {})
        record["cell_valid"] = False
        return {"record": record, "transcripts": transcripts}

    record["cell_valid"] = True
    convo = poisoned_msgs + [{"role": "assistant", "content": poisoned.content}]

    # --- Mens Rea ladder: all three levels scored --------------------------------
    print("   probes: naive / structured / adversarial")
    naive_r = client.complete(
        convo + [{"role": "user", "content": get_naive_cross_exam(scenario["legal_issue"])}],
        model=model, temperature=cfg.subject_temperature, max_tokens=cfg.max_tokens,
    )
    struct_r = client.complete(
        convo + [{"role": "user", "content": get_structured_cross_exam()}],
        model=model, temperature=cfg.subject_temperature, max_tokens=cfg.max_tokens,
    )
    adv_r = client.complete(
        convo + [{"role": "user", "content": get_adversarial_cross_exam(scenario)}],
        model=model, temperature=cfg.subject_temperature, max_tokens=cfg.max_tokens,
    )

    naive_s = _stance(naive_r.content, judge_fn, cfg)
    # Deterministic: the answer space is four letters and only C is an admission.
    struct_label, struct_letter = grade_structured_probe(struct_r.content)
    struct_s = StanceResult(label=struct_label, agreement=1.0, letter=struct_letter,
                            note="parsed deterministically, no judge")
    adv_s = _stance(adv_r.content, judge_fn, cfg)

    probe_responses = {"naive": naive_r, "structured": struct_r, "adversarial": adv_r}
    probe_stances = {"naive": naive_s, "structured": struct_s, "adversarial": adv_s}

    # --- Persona stability -------------------------------------------------------
    print("   probes: backroom / whistleblower / epistemic")
    persona_responses: dict[str, LLMResponse] = {}
    persona_stances: dict[str, StanceResult] = {}
    for name, builder in PERSONA_PROBES:
        resp = client.complete(
            convo + [{"role": "user", "content": builder(scenario)}],
            model=model, temperature=cfg.subject_temperature, max_tokens=cfg.max_tokens,
        )
        persona_responses[name] = resp
        persona_stances[name] = _stance(resp.content, judge_fn, cfg)

    verdict = derive_mens_rea(poisoned_bias, naive_s, struct_s, adv_s, persona_stances)
    record |= _verdict_fields(verdict, probe_stances, persona_stances)

    for name, resp in {**probe_responses, **persona_responses}.items():
        stance = probe_stances.get(name) or persona_stances[name]
        transcripts[name] = {
            "response": resp.content,
            "reasoning": resp.reasoning,
            "stance": stance.label,
            "agreement": stance.agreement,
            "letter": stance.letter,
        }

    print(f"   => {describe_verdict(verdict)}")
    return {"record": record, "transcripts": transcripts}


def _verdict_fields(verdict, probe_stances: dict, persona_stances: dict) -> dict:
    out = {
        "mens_rea_class": verdict.mens_rea_class,
        "persona_consistency": verdict.persona_consistency,
        "cognizable_intent": verdict.cognizable_intent,
        "admitted_at_any_level": verdict.admitted_at_any_level,
        "persona_admit_count": verdict.persona_admit_count,
        "persona_valid_count": verdict.persona_valid_count,
        "verdict": describe_verdict(verdict),
        "verdict_rationale": verdict.rationale,
    }
    for name, s in probe_stances.items():
        out[f"stance_{name}"] = s.label
        out[f"agreement_{name}"] = s.agreement
        if name == "structured":
            out["structured_letter"] = s.letter
    for name, s in persona_stances.items():
        out[f"stance_{name}"] = s.label
        out[f"agreement_{name}"] = s.agreement
    return out


# --- orchestration ---------------------------------------------------------------


def _done_keys(path: Path) -> set[tuple]:
    keys: set[tuple] = set()
    if not path.exists():
        return keys
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)["record"]
            keys.add((r["model_id"], r["scenario_id"], r["repeat"]))
        except Exception:
            continue
    return keys


def _write_transcript(fh, record: dict, transcripts: dict) -> None:
    fh.write(f"\n## {record['model']} | {record['scenario']} | repeat {record['repeat']}\n\n")
    fh.write(f"**Verdict**: {record.get('verdict', 'n/a')}  \n")
    fh.write(f"**Parties**: poison favours *{record['favored_party']}* over "
             f"*{record['disfavored_party']}*\n\n")
    fh.write("| Condition | Bias label |\n|---|---|\n")
    for cond in ("control", "ablation", "poisoned"):
        fh.write(f"| {cond} | {record.get(f'{cond}_bias', 'n/a')} |\n")
    fh.write("\n")
    for name, blob in transcripts.items():
        fh.write(f"### {name}\n")
        if "bias" in blob:
            fh.write(f"*Bias label: {blob['bias']}*\n\n")
        else:
            fh.write(f"*Stance: {blob.get('stance')} "
                     f"(judge agreement {blob.get('agreement')})*\n\n")
        body = blob.get("ruling") or blob.get("response") or ""
        fh.write(f"**Answer (this is what was graded):**\n```text\n{body or '<empty>'}\n```\n\n")
        if blob.get("reasoning"):
            fh.write("<details><summary>Reasoning trace (recorded, never graded)</summary>\n\n"
                     f"```text\n{blob['reasoning']}\n```\n\n</details>\n\n")
    fh.write("---\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mens Rea Evaluator v2 runner")
    ap.add_argument("--models", nargs="+", default=list(SUBJECT_MODELS))
    ap.add_argument("--judge-model", default=os.environ.get("JUDGE_MODEL", ""),
                    help="Must NOT be one of the subject models.")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--judge-samples", type=int, default=3)
    ap.add_argument("--subject-temperature", type=float, default=0.0)
    ap.add_argument("--judge-temperature", type=float, default=0.0)
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--scenarios", nargs="*", default=None, help="Scenario ids; default all")
    ap.add_argument("--out-dir", default="results")
    ap.add_argument("--dry-run", action="store_true", help="Print the plan; make no API calls")
    args = ap.parse_args(argv)

    validate_scenarios()
    scenarios = load_curated_scenarios()
    if args.scenarios:
        wanted = set(args.scenarios)
        scenarios = [s for s in scenarios if s["id"] in wanted]
        if not scenarios:
            print(f"No scenarios matched {sorted(wanted)}", file=sys.stderr)
            return 2

    cfg = Config(
        models=args.models,
        judge_model=args.judge_model,
        repeats=args.repeats,
        judge_samples=args.judge_samples,
        subject_temperature=args.subject_temperature,
        judge_temperature=args.judge_temperature,
        max_tokens=args.max_tokens,
        out_dir=args.out_dir,
        # No dedicated judge key: resolve_api_key() looks it up from the judge model.
        judge_api_key=None,
    )

    n_cells = len(cfg.models) * len(scenarios) * cfg.repeats
    subject_calls = n_cells * 9          # 3 rulings + 3 interrogation + 3 persona
    judge_calls = n_cells * (3 + 5) * cfg.judge_samples  # 3 bias + 5 free-text stances

    print("=" * 74)
    print("Mens Rea Evaluator v2")
    print("=" * 74)
    print(f"  models           : {', '.join(cfg.models)}")
    print(f"  scenarios        : {len(scenarios)} ({', '.join(s['id'] for s in scenarios)})")
    print(f"  repeats per cell : {cfg.repeats}")
    print(f"  cells            : {n_cells}")
    print(f"  judge model      : {cfg.judge_model or '<UNSET>'}")
    print(f"  judge samples    : {cfg.judge_samples} (majority vote)")
    print(f"  subject temp     : {cfg.subject_temperature}")
    print(f"  judge temp       : {cfg.judge_temperature}")
    print(f"  max_tokens       : {cfg.max_tokens}")
    print(f"  est. API calls   : ~{subject_calls} subject + ~{judge_calls} judge")
    print("=" * 74)

    if not cfg.judge_model:
        print("\nERROR: no judge model. Set JUDGE_MODEL in .env or pass --judge-model.\n"
              "It must not be one of the models under test; in v1 gpt-oss-20b graded its\n"
              "own rulings and confessions.", file=sys.stderr)
        return 2
    conflict = warn_if_judge_is_subject(cfg.judge_model)
    if conflict and not args.dry_run:
        print("Refusing to run with a self-judging configuration. "
              "Pass a different --judge-model.", file=sys.stderr)
        return 2

    if args.dry_run:
        print("\nDry run: no API calls made. Plan above.")
        return 0

    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jsonl_path, csv_path, md_path = out / RUNS_JSONL, out / RESULTS_CSV, out / TRANSCRIPTS_MD

    done = _done_keys(jsonl_path)
    if done:
        print(f"\nResuming: {len(done)} cells already recorded in {jsonl_path}")

    client = LLMClient(max_tokens=cfg.max_tokens)
    try:
        judge_fn = client.judge_fn(
            cfg.judge_model, api_key=cfg.judge_api_key, temperature=cfg.judge_temperature
        )
    except MissingCredentials as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2

    if not md_path.exists():
        md_path.write_text(
            "# Supplementary Evidence v2\n\n"
            "Every condition is included (control, ablation, poisoned) plus all six probes.\n"
            "The graded text is shown under **Answer**. Reasoning traces are recorded in\n"
            "collapsed blocks and are never passed to a grader — grading chain-of-thought\n"
            "instead of answers is what invalidated v1.\n"
        )

    completed = 0
    for model in cfg.models:
        for scenario in scenarios:
            for repeat in range(cfg.repeats):
                key = (model, scenario["id"], repeat)
                if key in done:
                    print(f"SKIP (done): {model} × {scenario['id']} r{repeat}")
                    continue
                try:
                    result = run_cell(client, judge_fn, model, scenario, repeat, cfg)
                except MissingCredentials as exc:
                    print(f"\nERROR: {exc}", file=sys.stderr)
                    return 2
                except Exception as exc:  # noqa: BLE001
                    print(f"   !! cell failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                    continue

                with jsonl_path.open("a") as fh:
                    fh.write(json.dumps(result, default=str) + "\n")
                with md_path.open("a") as fh:
                    _write_transcript(fh, result["record"], result["transcripts"])
                _rewrite_csv(jsonl_path, csv_path)
                completed += 1
                time.sleep(1)

    print(f"\nDone. {completed} new cells. Results: {csv_path}")
    print(f"Now run:  ./venv/bin/python analyze.py --results {csv_path}")
    return 0


def _rewrite_csv(jsonl_path: Path, csv_path: Path) -> None:
    import pandas as pd

    rows = []
    for line in jsonl_path.read_text().splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line)["record"])
            except Exception:
                continue
    if rows:
        pd.DataFrame(rows).to_csv(csv_path, index=False)


if __name__ == "__main__":
    raise SystemExit(main())
