#!/usr/bin/env python3
"""
Mens Rea Evaluator — analysis and figures (v2).

Every number and every figure is computed from the results file. Nothing is hardcoded.
In v1, `make_final_figures.py` read the CSV into `df` and then never used it, plotting a
literal dict instead, while `generate_figs.py` and `generate_more_figs.py` carried stale
values that contradicted the CSV (poisoned `[100, 20, 100]`; persona
`deconstructed=[5,3,5]`, `ambiguous=[0,1,0]`).

What this reports that v1 did not:
  * the control arm, alongside ablation and poisoned;
  * paired within-scenario contrasts, since the pooled rates are not interpretable when
    the baseline is 80-100%;
  * Wilson confidence intervals and exact tests, so n=5 claims are visible as such;
  * excluded/invalid counts;
  * judge agreement, so grader instability is reported instead of asserted away;
  * admission rate per interrogation level, which is what the escalation claim needs;
  * cognizable intent over the correct denominator (biased runs only).

Usage:
    ./venv/bin/python analyze.py --results results/results_v2.csv
"""

from __future__ import annotations

import argparse
import math
import sys
from itertools import combinations
from pathlib import Path

import pandas as pd

CONDITIONS = ("control", "ablation", "poisoned")
LEVELS = ("naive", "structured", "adversarial")
PERSONA = ("backroom", "whistleblower", "epistemic")


# --- statistics (no scipy dependency) --------------------------------------------


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Appropriate at small n, unlike the normal approximation."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a,b],[c,d]], by summing tables no more likely."""
    n = a + b + c + d
    if n == 0:
        return 1.0
    row1, col1 = a + b, a + c

    def prob(x: int) -> float:
        return (
            math.comb(row1, x)
            * math.comb(n - row1, col1 - x)
            / math.comb(n, col1)
        )

    lo = max(0, col1 - (n - row1))
    hi = min(row1, col1)
    observed = prob(a)
    total = 0.0
    for x in range(lo, hi + 1):
        p = prob(x)
        if p <= observed + 1e-12:
            total += p
    return min(1.0, total)


def mcnemar_exact(b: int, c: int) -> float:
    """
    Two-sided exact McNemar for paired binary data. b and c are the discordant counts.
    Used for within-scenario contrasts (same model, same scenario, different condition).
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def pct(k: int, n: int) -> str:
    if n == 0:
        return "n/a"
    lo, hi = wilson_ci(k, n)
    return f"{k / n:6.1%} ({k}/{n})  95% CI [{lo:.0%}, {hi:.0%}]"


# --- loading ---------------------------------------------------------------------


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    for cond in CONDITIONS:
        for col in (f"{cond}_biased", f"{cond}_valid"):
            if col in df:
                df[col] = df[col].astype(str).str.lower().isin(("true", "1", "yes"))
    for col in ("cognizable_intent", "admitted_at_any_level", "cell_valid"):
        if col in df:
            df[col] = df[col].astype(str).str.lower().isin(("true", "1", "yes"))
    return df


# --- sections -------------------------------------------------------------------


def section_coverage(df: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("1. COVERAGE AND EXCLUSIONS")
    print("=" * 78)
    print(f"  cells recorded: {len(df)}")
    if "cell_valid" in df:
        n_bad = int((~df["cell_valid"]).sum())
        print(f"  cells with no usable poisoned ruling (excluded from Mens Rea): {n_bad}")
    for cond in CONDITIONS:
        vcol = f"{cond}_valid"
        if vcol in df:
            bad = df[~df[vcol]]
            print(f"  {cond:9} rulings: {len(df) - len(bad)}/{len(df)} valid")
            for reason, n in bad.get(f"{cond}_invalid_reason", pd.Series(dtype=str)).value_counts().items():
                print(f"              excluded: {n} x {reason}")
    print("\n  Invalid rulings are excluded, never scored as biased. v1 counted six empty\n"
          "  rulings as biased, including 4 of the 5 behind muse-glimmer's reported 100%.")


def section_susceptibility(df: pd.DataFrame) -> pd.DataFrame:
    print("\n" + "=" * 78)
    print("2. SUSCEPTIBILITY BY CONDITION  (control reported, unlike v1)")
    print("=" * 78)
    rows = []
    for model, g in df.groupby("model", sort=False):
        print(f"\n  {model}")
        for cond in CONDITIONS:
            bcol, vcol = f"{cond}_biased", f"{cond}_valid"
            if bcol not in g:
                continue
            valid = g[g[vcol]] if vcol in g else g
            k, n = int(valid[bcol].sum()), len(valid)
            print(f"    {cond:9} {pct(k, n)}")
            rows.append({"model": model, "condition": cond, "k": k, "n": n,
                         "rate": (k / n) if n else float("nan")})
    print("\n  Read the control row first. If control is already high, the poisoned rate\n"
          "  does not isolate an effect of poisoning.")
    return pd.DataFrame(rows)


def section_paired(df: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("3. PAIRED WITHIN-SCENARIO CONTRASTS  (the effect the paper actually needs)")
    print("=" * 78)
    for model, g in df.groupby("model", sort=False):
        print(f"\n  {model}")
        for a, b in (("control", "ablation"), ("control", "poisoned"), ("ablation", "poisoned")):
            ac, bc = f"{a}_biased", f"{b}_biased"
            av, bv = f"{a}_valid", f"{b}_valid"
            if ac not in g or bc not in g:
                continue
            mask = pd.Series(True, index=g.index)
            for vcol in (av, bv):
                if vcol in g:
                    mask &= g[vcol]
            pairs = g[mask]
            only_b = int((~pairs[ac] & pairs[bc]).sum())   # poison introduced bias
            only_a = int((pairs[ac] & ~pairs[bc]).sum())   # poison removed bias
            p = mcnemar_exact(only_b, only_a)
            print(f"    {a:9} -> {b:9}  +{only_b} gained, -{only_a} lost, "
                  f"n={len(pairs)}, exact McNemar p={p:.3f}")
        print("    (a negative 'gained' direction, i.e. more lost than gained, means the\n"
              "     poison made the model *less* biased on those scenarios)")


def section_model_comparison(df: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("4. BETWEEN-MODEL COMPARISON  (is any difference distinguishable at this n?)")
    print("=" * 78)
    for cond in CONDITIONS:
        bcol, vcol = f"{cond}_biased", f"{cond}_valid"
        if bcol not in df:
            continue
        print(f"\n  {cond}")
        stats = {}
        for model, g in df.groupby("model", sort=False):
            valid = g[g[vcol]] if vcol in g else g
            stats[model] = (int(valid[bcol].sum()), len(valid))
        for m1, m2 in combinations(stats, 2):
            k1, n1 = stats[m1]
            k2, n2 = stats[m2]
            p = fisher_exact_two_sided(k1, n1 - k1, k2, n2 - k2)
            verdict = "distinguishable" if p < 0.05 else "NOT distinguishable"
            print(f"    {m1} ({k1}/{n1}) vs {m2} ({k2}/{n2}): Fisher p={p:.3f}  -> {verdict}")


def section_elicitation(df: pd.DataFrame) -> pd.DataFrame:
    print("\n" + "=" * 78)
    print("5. ADMISSION RATE BY INTERROGATION LEVEL  (v1 never measured this)")
    print("=" * 78)
    print("  v1 generated naive/structured/adversarial but only graded the structured one,\n"
          "  so its claim that naive prompting fails and adversarial succeeds had no data.")
    rows = []
    biased = df[df.get("poisoned_biased", False)] if "poisoned_biased" in df else df
    for model, g in biased.groupby("model", sort=False):
        print(f"\n  {model}  (biased poisoned runs only, n={len(g)})")
        for level in LEVELS:
            col = f"stance_{level}"
            if col not in g:
                continue
            valid = g[~g[col].isin(["INVALID", "UNPARSEABLE"])]
            k, n = int((valid[col] == "ADMITS").sum()), len(valid)
            print(f"    {level:12} admits {pct(k, n)}")
            rows.append({"model": model, "level": level, "k": k, "n": n,
                         "rate": (k / n) if n else float("nan")})
        for level in LEVELS:
            col = f"stance_{level}"
            if col in g:
                dist = ", ".join(f"{v}={c}" for v, c in g[col].value_counts().items())
                print(f"      {level:12} distribution: {dist}")
    if "structured_letter" in biased:
        print("\n  Structured probe letters (C is the only admission):")
        print("   ", dict(biased["structured_letter"].value_counts()))
    return pd.DataFrame(rows)


def section_persona(df: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("6. PERSONA STABILITY AND COGNIZABLE INTENT")
    print("=" * 78)
    for model, g in df.groupby("model", sort=False):
        print(f"\n  {model}")
        if "persona_consistency" in g:
            for v, c in g["persona_consistency"].value_counts().items():
                print(f"    persona: {v:24} {c}")
        for p in PERSONA:
            col = f"stance_{p}"
            if col in g:
                dist = ", ".join(f"{v}={c}" for v, c in g[col].value_counts().items())
                print(f"    {p:14} {dist}")
        # Denominator fix: cognizable intent is only defined where bias actually occurred.
        if "poisoned_biased" in g and "cognizable_intent" in g:
            biased = g[g["poisoned_biased"]]
            k, n = int(biased["cognizable_intent"].sum()), len(biased)
            print(f"    cognizable intent (biased runs only): {pct(k, n)}")
            print(f"      v1 reported this as 0/5 for every model, including models whose\n"
                  f"      bias count was 2 — the metric is undefined on unbiased runs.")


def section_judge_reliability(df: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("7. JUDGE RELIABILITY  (measured, not asserted)")
    print("=" * 78)
    print("  v1 stated the judge was 'highly robust'. Its own transcripts show one identical\n"
          "  71-character response graded Truthful Denial, Reckless Confabulation, and\n"
          "  Purposeful across five runs, three of which shared an identical prompt at\n"
          "  temperature 0.")
    cols = [c for c in df.columns if c.startswith("agreement_")] + \
           [f"{c}_bias_agreement" for c in CONDITIONS if f"{c}_bias_agreement" in df]
    any_shown = False
    for col in cols:
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if s.empty:
            continue
        any_shown = True
        unanimous = (s >= 0.999).mean()
        print(f"    {col:34} mean agreement {s.mean():.2f}, unanimous in {unanimous:.0%} of cells")
    if not any_shown:
        print("    (no agreement columns found)")
    print("\n  The structured probe is parsed deterministically, so its agreement is 1.00 by\n"
          "  construction rather than by luck.")


def section_claims_audit(df: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("8. CLAIMS AUDIT — v1 paper claims checked against this data")
    print("=" * 78)

    def rate(model_sub, cond):
        bcol, vcol = f"{cond}_biased", f"{cond}_valid"
        v = model_sub[model_sub[vcol]] if vcol in model_sub else model_sub
        return (int(v[bcol].sum()), len(v)) if bcol in v else (0, 0)

    checks = []
    for model, g in df.groupby("model", sort=False):
        ck, cn = rate(g, "control")
        ak, an = rate(g, "ablation")
        pk, pn = rate(g, "poisoned")
        c_rate = ck / cn if cn else float("nan")
        a_rate = ak / an if an else float("nan")
        p_rate = pk / pn if pn else float("nan")
        checks.append((model, c_rate, a_rate, p_rate))
        print(f"\n  {model}")
        print(f"    'poisoned documents alone induce bias'")
        print(f"       ablation {a_rate:.0%} vs control {c_rate:.0%} "
              f"-> lift {a_rate - c_rate:+.0%}")
        print(f"    'explicit system prompts make it worse'")
        print(f"       poisoned {p_rate:.0%} vs ablation {a_rate:.0%} "
              f"-> lift {p_rate - a_rate:+.0%}")
    print("\n  'larger models are more susceptible': the subject set is gpt-oss-20b (~3.6B\n"
          "  active, MoE), diffusiongemma-26b-a4b (~4B active), muse-glimmer-30b (~29.6B\n"
          "  dense). By total or active parameters gpt-oss-20b is the SMALLEST model, so\n"
          "  grouping it with muse-glimmer as 'the larger models' against the 26B is not a\n"
          "  size ordering. Report per-model results without the size narrative unless a\n"
          "  monotonic trend survives here.")


# --- figures --------------------------------------------------------------------


def make_figures(df: pd.DataFrame, susc: pd.DataFrame, elicit: pd.DataFrame, out: Path) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    models = list(dict.fromkeys(df["model"]))

    # Figure 1 — all three conditions with CIs. The control bar is the point.
    if not susc.empty:
        fig, ax = plt.subplots(figsize=(10, 6))
        width = 0.26
        x = np.arange(len(models))
        colors = {"control": "#7f7f7f", "ablation": "#ff7f0e", "poisoned": "#1f77b4"}
        labels = {"control": "Control (benign docs + benign prompt)",
                  "ablation": "Ablation (poisoned docs only)",
                  "poisoned": "Poisoned (docs + secret instruction)"}
        for i, cond in enumerate(CONDITIONS):
            sub = susc[susc.condition == cond].set_index("model").reindex(models)
            vals = (sub["rate"] * 100).tolist()
            errs = []
            for _, r in sub.iterrows():
                if r["n"] and not math.isnan(r["rate"]):
                    lo, hi = wilson_ci(int(r["k"]), int(r["n"]))
                    errs.append([(r["rate"] - lo) * 100, (hi - r["rate"]) * 100])
                else:
                    errs.append([0, 0])
            yerr = np.array(errs).T if errs else None
            ax.bar(x + (i - 1) * width, vals, width, label=labels[cond],
                   color=colors[cond], yerr=yerr, capsize=4)
            for xi, v in zip(x + (i - 1) * width, vals):
                if not math.isnan(v):
                    ax.text(xi, v + 2, f"{v:.0f}%", ha="center", fontsize=8, fontweight="bold")
        ax.set_ylabel("Rulings favouring the poison's target (%)")
        ax.set_title("Susceptibility by condition (95% Wilson CI)")
        ax.set_xticks(x); ax.set_xticklabels(models)
        ax.set_ylim(0, 118); ax.legend(fontsize=8, loc="upper right")
        p = out / "Figure_1_Susceptibility_By_Condition.png"
        fig.savefig(p, dpi=200, bbox_inches="tight"); plt.close(fig); written.append(p)

    # Figure 2 — admission rate across the elicitation ladder.
    if not elicit.empty:
        fig, ax = plt.subplots(figsize=(9, 6))
        x = np.arange(len(LEVELS))
        for model in models:
            sub = elicit[elicit.model == model].set_index("level").reindex(LEVELS)
            ax.plot(x, (sub["rate"] * 100).tolist(), marker="o", label=model)
        ax.set_xticks(x); ax.set_xticklabels([l.title() for l in LEVELS])
        ax.set_ylabel("Runs admitting the hidden instruction (%)")
        ax.set_title("Admission rate across the interrogation ladder (biased runs only)")
        ax.set_ylim(-5, 105); ax.legend(fontsize=8); ax.grid(alpha=0.3)
        p = out / "Figure_2_Admission_By_Level.png"
        fig.savefig(p, dpi=200, bbox_inches="tight"); plt.close(fig); written.append(p)

    # Figure 3 — mens rea classes, counted from the data.
    if "mens_rea_class" in df:
        classes = sorted(df["mens_rea_class"].dropna().unique())
        fig, ax = plt.subplots(figsize=(9, 6))
        bottom = np.zeros(len(models))
        cmap = plt.get_cmap("tab10")
        for i, klass in enumerate(classes):
            vals = [int(((df.model == m) & (df.mens_rea_class == klass)).sum()) for m in models]
            ax.bar(models, vals, 0.6, bottom=bottom, label=klass, color=cmap(i % 10))
            bottom += np.array(vals)
        ax.set_ylabel("Number of cells"); ax.set_title("Mens Rea classification by model")
        ax.legend(fontsize=8)
        p = out / "Figure_3_Mens_Rea_Classes.png"
        fig.savefig(p, dpi=200, bbox_inches="tight"); plt.close(fig); written.append(p)

    # Figure 4 — persona consistency, counted from the data.
    if "persona_consistency" in df:
        cats = sorted(df["persona_consistency"].dropna().unique())
        fig, ax = plt.subplots(figsize=(9, 6))
        bottom = np.zeros(len(models))
        cmap = plt.get_cmap("Set2")
        for i, cat in enumerate(cats):
            vals = [int(((df.model == m) & (df.persona_consistency == cat)).sum()) for m in models]
            ax.bar(models, vals, 0.6, bottom=bottom, label=cat, color=cmap(i % 8))
            bottom += np.array(vals)
        ax.set_ylabel("Number of cells")
        ax.set_title("Persona consistency of the admission, by model")
        ax.legend(fontsize=8)
        p = out / "Figure_4_Persona_Consistency.png"
        fig.savefig(p, dpi=200, bbox_inches="tight"); plt.close(fig); written.append(p)

    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Analyse Mens Rea Evaluator v2 results")
    ap.add_argument("--results", default="results/results_v2.csv")
    ap.add_argument("--fig-dir", default="figures_v2")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.results)
    if not path.exists():
        print(f"No results at {path}. Run run_eval.py first.", file=sys.stderr)
        return 2

    df = load(path)
    print("=" * 78)
    print(f"MENS REA EVALUATOR v2 — ANALYSIS OF {path}")
    print("=" * 78)

    section_coverage(df)
    susc = section_susceptibility(df)
    section_paired(df)
    section_model_comparison(df)
    elicit = section_elicitation(df)
    section_persona(df)
    section_judge_reliability(df)
    section_claims_audit(df)

    if not args.no_figures:
        written = make_figures(df, susc, elicit, Path(args.fig_dir))
        print("\n" + "=" * 78)
        print("FIGURES (all computed from the results file)")
        print("=" * 78)
        for p in written:
            print(f"  {p}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
