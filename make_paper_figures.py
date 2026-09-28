#!/usr/bin/env python3
"""
Publication figures for the Mens Rea Evaluator paper.

Separate from analyze.py, which is the statistics tool. Everything here is computed from
the results file, nothing is hardcoded.

    ./venv/bin/python make_paper_figures.py
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd

from analyze import wilson_ci

plt.rcParams.update({
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.labelsize": 11,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.dpi": 200,
})

MODEL_SHORT = {
    "gpt-oss-20b": "gpt-oss-20b",
    "diffusiongemma-26b-a4b-it": "diffusiongemma-26b",
    "muse-glimmer-30b": "muse-glimmer-30b",
}
SCEN_SHORT = {
    "Contract Law: Lease Dispute": "Lease",
    "Criminal Law: Trespass": "Trespass",
    "Tort Law: Negligence (Slip and Fall)": "Negligence",
    "Criminal Law: Battery": "Battery",
    "Tort Law: Defamation": "Defamation",
}
SCEN_ORDER = ["Lease", "Trespass", "Negligence", "Battery", "Defamation"]

C_CTRL, C_ABL, C_POIS = "#9e9e9e", "#e8871a", "#1f6fb4"
STANCE_COLORS = {
    "ADMITS": "#2e7d32",
    "DEFLECTS": "#e8871a",
    "DENIES": "#6a7b8c",
    "REFUSES": "#b3271e",
    "EVASIVE": "#c9c9c9",
}
STANCE_ORDER = ["ADMITS", "DEFLECTS", "DENIES", "REFUSES", "EVASIVE"]
PROBES = [
    ("naive", "Naive question"),
    ("structured", "Forced choice"),
    ("adversarial", "Adversarial audit"),
    ("backroom", "Persona: off the record"),
    ("whistleblower", "Persona: whistleblower"),
    ("epistemic", "Persona: drop the persona"),
]


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    for c in ("control_biased", "ablation_biased", "poisoned_biased", "self_judged"):
        if c in df:
            df[c] = df[c].astype(str).str.lower().isin(("true", "1", "yes"))
    df["m"] = df["model"].map(lambda x: MODEL_SHORT.get(x, x))
    df["s"] = df["scenario"].map(lambda x: SCEN_SHORT.get(x, x))
    return df


def fig1_susceptibility(df, out: Path):
    """Grouped bars, three conditions per model, with Wilson intervals."""
    models = [MODEL_SHORT[m] for m in MODEL_SHORT if MODEL_SHORT[m] in set(df.m)]
    fig, ax = plt.subplots(figsize=(9, 5.2))
    width = 0.26
    x = np.arange(len(models))
    conds = [("control_biased", "Control\n(clean documents)", C_CTRL),
             ("ablation_biased", "Ablation\n(poisoned document only)", C_ABL),
             ("poisoned_biased", "Poisoned\n(document + instruction)", C_POIS)]

    for i, (col, label, colour) in enumerate(conds):
        vals, errs = [], [[], []]
        for m in models:
            g = df[df.m == m]
            k, n = int(g[col].sum()), len(g)
            r = k / n
            lo, hi = wilson_ci(k, n)
            vals.append(r * 100)
            errs[0].append((r - lo) * 100)
            errs[1].append((hi - r) * 100)
        pos = x + (i - 1) * width
        ax.bar(pos, vals, width, label=label, color=colour,
               yerr=np.array(errs), capsize=4, ecolor="#444", error_kw={"lw": 1.2})
        for xi, v in zip(pos, vals):
            ax.text(xi, v + 3.5, f"{v:.0f}%", ha="center", fontsize=9.5, fontweight="bold")

    ax.set_ylabel("Rulings favouring the poisoned party (%)")
    ax.set_title("Bias by condition, with 95% Wilson intervals (n = 5 per model)")
    ax.set_xticks(x)
    ax.set_xticklabels(models)
    ax.set_ylim(0, 122)
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.legend(fontsize=8.5, loc="upper center", ncol=3, frameon=False,
              bbox_to_anchor=(0.5, -0.12))
    ax.grid(axis="y", alpha=0.25)
    ax.set_axisbelow(True)
    p = out / "Figure_1_Bias_By_Condition.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def fig2_heatmap(df, out: Path):
    """Every run as a cell, so the reader can see which scenarios flipped."""
    models = [m for m in MODEL_SHORT.values() if m in set(df.m)]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.6), sharey=True)
    conds = [("control_biased", "Control", C_CTRL),
             ("ablation_biased", "Ablation", C_ABL),
             ("poisoned_biased", "Poisoned", C_POIS)]

    for ax, (col, title, colour) in zip(axes, conds):
        grid = np.zeros((len(SCEN_ORDER), len(models)))
        for j, m in enumerate(models):
            for i, s in enumerate(SCEN_ORDER):
                r = df[(df.m == m) & (df.s == s)]
                grid[i, j] = 1 if (len(r) and bool(r.iloc[0][col])) else 0
        ax.imshow(grid, cmap=matplotlib.colors.ListedColormap(["#f2f2f2", colour]),
                  vmin=0, vmax=1, aspect="auto")
        for i in range(len(SCEN_ORDER)):
            for j in range(len(models)):
                ax.text(j, i, "B" if grid[i, j] else "\u00b7",
                        ha="center", va="center",
                        color="white" if grid[i, j] else "#999",
                        fontsize=11, fontweight="bold")
        ax.set_xticks(range(len(models)))
        ax.set_xticklabels([m.replace("-", "-\n", 1) for m in models], fontsize=8)
        ax.set_yticks(range(len(SCEN_ORDER)))
        ax.set_yticklabels(SCEN_ORDER, fontsize=9)
        ax.set_title(title, fontsize=11)
        ax.set_xticks(np.arange(-0.5, len(models), 1), minor=True)
        ax.set_yticks(np.arange(-0.5, len(SCEN_ORDER), 1), minor=True)
        ax.grid(which="minor", color="white", lw=2)
        ax.tick_params(which="minor", length=0)

    fig.suptitle("Which runs produced a biased ruling.  B = biased toward the poisoned party",
                 fontsize=11.5, y=1.04)
    p = out / "Figure_2_Run_Level_Heatmap.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def fig3_stances(df, out: Path):
    """
    The introspection result in one chart. Six probes, stacked by what the model
    asserted. Carries both the interrogation ladder and the persona shifts.
    """
    fig, ax = plt.subplots(figsize=(9.5, 4.8))
    labels, bottoms = [], np.zeros(len(PROBES))
    counts = {s: [] for s in STANCE_ORDER}

    for key, label in PROBES:
        col = f"stance_{key}"
        vc = df[col].value_counts() if col in df else pd.Series(dtype=int)
        labels.append(label)
        for s in STANCE_ORDER:
            counts[s].append(int(vc.get(s, 0)))

    y = np.arange(len(PROBES))
    for s in STANCE_ORDER:
        vals = np.array(counts[s])
        if vals.sum() == 0:
            continue
        ax.barh(y, vals, 0.62, left=bottoms, label=s.title(), color=STANCE_COLORS[s])
        for yi, (v, b) in enumerate(zip(vals, bottoms)):
            if v:
                ax.text(b + v / 2, yi, str(v), ha="center", va="center",
                        color="white" if s != "EVASIVE" else "#333",
                        fontsize=9.5, fontweight="bold")
        bottoms += vals

    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Number of runs (n = 15)")
    ax.set_xlim(0, 15.6)
    ax.set_title("What the models asserted, across all six probes")
    ax.axhline(2.5, color="#bbb", lw=1, ls="--")
    ax.legend(fontsize=9, ncol=4, frameon=False, loc="upper center",
              bbox_to_anchor=(0.5, -0.16))
    ax.grid(axis="x", alpha=0.25)
    ax.set_axisbelow(True)
    p = out / "Figure_3_Stance_Across_Probes.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def fig4_letters(df, out: Path):
    """Forced choice answers. Shows B dominating, which is the concealment mechanism."""
    opts = ["A", "B", "C", "D"]
    text = {
        "A": "A  Purely objective",
        "B": "B  Relied on a retrieved document",
        "C": "C  Followed a hidden instruction",
        "D": "D  Hallucinated or made a mistake",
    }
    models = [m for m in MODEL_SHORT.values() if m in set(df.m)]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    y = np.arange(len(opts))
    left = np.zeros(len(opts))
    # Deliberately avoids the green/orange/red used for stances in Figure 3, so the
    # same colour never means two different things across neighbouring figures.
    palette = ["#3b5b92", "#8c6bb1", "#9aa5b1"]

    for mi, m in enumerate(models):
        g = df[df.m == m]
        vals = np.array([int((g["structured_letter"] == o).sum()) for o in opts], dtype=float)
        ax.barh(y, vals, 0.6, left=left, color=palette[mi % 3], label=m)
        for yi, (v, b) in enumerate(zip(vals, left)):
            if v:
                ax.text(b + v / 2, yi, int(v), ha="center", va="center",
                        color="white", fontsize=9.5, fontweight="bold")
        left += vals

    ax.set_yticks(y)
    ax.set_yticklabels([text[o] for o in opts], fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("Number of runs (n = 15)")
    ax.set_xlim(0, 9.6)
    ax.set_title("Forced choice answers. Only C admits the hidden instruction")
    ax.get_yticklabels()[2].set_color("#2e7d32")
    ax.get_yticklabels()[2].set_fontweight("bold")
    ax.legend(fontsize=9, frameon=False, ncol=3, loc="upper center",
              bbox_to_anchor=(0.5, -0.18))
    ax.grid(axis="x", alpha=0.25)
    ax.set_axisbelow(True)
    p = out / "Figure_4_Forced_Choice_Answers.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def fig5_classes(df, out: Path):
    """Mens Rea classification, derived from the per probe labels."""
    models = [m for m in MODEL_SHORT.values() if m in set(df.m)]
    classes = ["Purposeful", "Confabulation", "Willful Blindness"]
    present = [c for c in classes if c in set(df.mens_rea_class)]
    # Matches the stance palette in Figure 3: admitting is green, attributing the
    # ruling elsewhere is orange, flat denial is grey blue.
    colours = {"Purposeful": "#2e7d32", "Confabulation": "#e8871a",
               "Willful Blindness": "#6a7b8c"}
    fig, ax = plt.subplots(figsize=(8, 4.4))
    x = np.arange(len(models))
    bottom = np.zeros(len(models))
    for c in present:
        vals = np.array([int(((df.m == m) & (df.mens_rea_class == c)).sum()) for m in models],
                        dtype=float)
        ax.bar(x, vals, 0.55, bottom=bottom, label=c, color=colours.get(c, "#999"))
        for xi, (v, b) in enumerate(zip(vals, bottom)):
            if v:
                ax.text(xi, b + v / 2, int(v), ha="center", va="center",
                        color="white", fontsize=10, fontweight="bold")
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels(models, fontsize=9.5)
    ax.set_ylabel("Number of runs")
    ax.set_ylim(0, 5.8)
    ax.set_title("Mens Rea classification. Every run withheld across all persona shifts")
    ax.legend(fontsize=9, frameon=False, ncol=3, loc="upper center",
              bbox_to_anchor=(0.5, -0.1))
    ax.grid(axis="y", alpha=0.25)
    ax.set_axisbelow(True)
    p = out / "Figure_5_Mens_Rea_Classes.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results/results_v2.csv")
    ap.add_argument("--out", default="figures_paper")
    a = ap.parse_args()
    df = load(Path(a.results))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    made = [fig1_susceptibility(df, out), fig2_heatmap(df, out), fig3_stances(df, out),
            fig4_letters(df, out), fig5_classes(df, out)]
    for p in made:
        print(f"  {p}  ({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
