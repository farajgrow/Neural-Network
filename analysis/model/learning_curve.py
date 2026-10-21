"""Learning curve: does the GRU (or its best baseline) improve with more stories?

Usage (from the repo root):  python -m analysis.model.learning_curve

Same 5-fold CV by story as train.py. In each fold the training stories are subsampled
to 25%, 50%, 75% and 100% (by story, stratified by scam type, seeded and nested: each
smaller subset is contained in the larger ones). Every model is refitted on the subset
exactly as in train.py (same 15% early-stopping / tuning split carved from the subset)
and always scored on the full test fold.

Models: the GRU, markov1_by_type (best next-move baseline) and logistic (best loss
baseline). Writes analysis/model/learning_curve.json and prints a table.

The extrapolation to 2x and 5x data fits metric = a + b * ln(n_train_stories) to the
four fraction means. It is a rough estimate only: four points, one dataset, and
learning curves usually flatten faster than a log-linear fit assumes.
"""

from __future__ import annotations

import json
import math
import random
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

from analysis.model.data import Story, kfold_by_story, load_dataset
from analysis.model.gru import GRUConfig
from analysis.model.train import MODEL_NAME, N_FOLDS, SEED, evaluate_fold, fmt, summarize

OUT_PATH = Path(__file__).resolve().parent / "learning_curve.json"
FRACTIONS = (0.25, 0.5, 0.75, 1.0)
EXTRAPOLATE = (2.0, 5.0)
# (section, model, metric, higher is better)
METRICS = (
    ("next_step", "markov1_by_type", "top1", True),
    ("next_step", "gru", "top1", True),
    ("next_step", "markov1_by_type", "log_loss", False),
    ("next_step", "gru", "log_loss", False),
    ("loss_all_prefixes", "logistic", "auc", True),
    ("loss_all_prefixes", "gru", "auc", True),
    ("loss_all_prefixes", "logistic", "brier", False),
    ("loss_all_prefixes", "gru", "brier", False),
)
GAPS = (("next_step", "top1", "markov1_by_type"), ("next_step", "log_loss", "markov1_by_type"),
        ("loss_all_prefixes", "auc", "logistic"), ("loss_all_prefixes", "brier", "logistic"))


def subsample_by_story(stories: list[Story], fraction: float, seed: int) -> list[Story]:
    """Keep `fraction` of the stories of each scam type (at least one per type), seeded.

    Each type's stories are shuffled once with `seed`, and a fraction takes the first
    round(fraction * n) of them, so smaller fractions are subsets of larger ones.
    """
    by_type: dict[str, list[Story]] = defaultdict(list)
    for story in sorted(stories, key=lambda s: s.id):
        by_type[story.scam_type].append(story)
    rng = random.Random(seed)
    keep: set[str] = set()
    for scam_type in sorted(by_type):
        group = by_type[scam_type]
        rng.shuffle(group)
        n = max(1, round(fraction * len(group)))
        keep.update(s.id for s in group[:n])
    return [s for s in stories if s.id in keep]


def log_fit(ns: list[float], values: list[float]) -> tuple[float, float]:
    """Least-squares a, b for value = a + b * ln(n)."""
    b, a = np.polyfit(np.log(ns), values, 1)
    return float(a), float(b)


def run() -> dict:
    stories, vocab = load_dataset()
    config = GRUConfig(seed=SEED)
    per_fold = []
    for f, (train, test) in enumerate(kfold_by_story(stories, k=N_FOLDS, seed=SEED)):
        row = {}
        for fraction in FRACTIONS:
            subset = subsample_by_story(train, fraction, seed=SEED + f)
            fold, _ = evaluate_fold(subset, test, vocab, config)
            row[str(fraction)] = {"n_train_stories": len(subset),
                                  **{f"{sec}.{model}.{metric}": fold[sec][model][metric]
                                     for sec, model, metric, _ in METRICS}}
        per_fold.append(row)
        print(f"fold {f + 1}/{N_FOLDS} done")

    curve = {}
    for fraction in FRACTIONS:
        key = str(fraction)
        entry = {"n_train_stories": summarize([r[key]["n_train_stories"] for r in per_fold])}
        for sec, model, metric, _ in METRICS:
            entry[f"{sec}.{model}.{metric}"] = summarize([r[key][f"{sec}.{model}.{metric}"] for r in per_fold])
        for sec, metric, base in GAPS:   # paired per fold: same test stories
            entry[f"gap.{sec}.{metric}.gru_minus_{base}"] = summarize(
                [r[key][f"{sec}.gru.{metric}"] - r[key][f"{sec}.{base}.{metric}"] for r in per_fold])
        curve[key] = entry

    ns = [curve[str(fr)]["n_train_stories"]["mean"] for fr in FRACTIONS]
    extrapolation = {}
    for sec, model, metric, _ in METRICS:
        name = f"{sec}.{model}.{metric}"
        a, b = log_fit(ns, [curve[str(fr)][name]["mean"] for fr in FRACTIONS])
        extrapolation[name] = {"slope_per_doubling": b * math.log(2),
                               **{f"x{m:g}": a + b * math.log(m * ns[-1]) for m in EXTRAPOLATE}}
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model_name": MODEL_NAME, "folds": N_FOLDS, "seed": SEED, "fractions": list(FRACTIONS),
        "note": "Each fraction of the fold's training stories, subsampled by story and stratified by scam type; "
                "always scored on the full test fold. Extrapolation = least-squares fit of metric vs "
                "ln(n_train_stories) on the 4 fraction means; a rough estimate only.",
        "curve": curve, "extrapolation_rough": extrapolation, "per_fold": per_fold,
    }


def table(results: dict) -> str:
    cols = [(f"{sec}.{model}.{metric}", f"{model} {metric}") for sec, model, metric, _ in METRICS]
    lines = []
    for title, chosen in (("Next scam move", cols[:4]), ("Loss risk (all known-outcome prefixes)", cols[4:])):
        lines += [title, f"{'fraction':<9} {'stories':>8} " + " ".join(f"{label:>22}" for _, label in chosen)]
        for fr in results["fractions"]:
            e = results["curve"][str(fr)]
            lines.append(f"{fr:<9} {e['n_train_stories']['mean']:>8.0f} "
                         + " ".join(f"{fmt(e[name]):>22}" for name, _ in chosen))
        lines.append("")
    lines.append("Paired gap, GRU minus baseline (mean ± std over folds)")
    gap_names = [k for k in results["curve"]["1.0"] if k.startswith("gap.")]
    lines.append(f"{'fraction':<9} " + " ".join(f"{n.split('.', 2)[2]:>34}" for n in gap_names))
    for fr in results["fractions"]:
        e = results["curve"][str(fr)]
        lines.append(f"{fr:<9} " + " ".join(f"{fmt(e[n]):>34}" for n in gap_names))
    lines += ["", "Rough log-linear extrapolation (NOT a measurement)",
              f"{'metric':<40} {'per doubling':>13} {'2x data':>9} {'5x data':>9}"]
    for name, x in results["extrapolation_rough"].items():
        lines.append(f"{name:<40} {x['slope_per_doubling']:>+13.3f} {x['x2']:>9.3f} {x['x5']:>9.3f}")
    return "\n".join(lines)


def main() -> None:
    torch.set_num_threads(1)
    start = time.time()
    results = run()
    OUT_PATH.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print()
    print(table(results))
    print(f"\ndone in {time.time() - start:.1f}s -> {OUT_PATH.name}")


if __name__ == "__main__":
    main()
