"""Cross-validate the baselines and the GRU, then train and save the final GRU.

Usage (from the repo root):  python -m analysis.model.train

Writes analysis/model/results.json, analysis/model/gru.pt and analysis/model/model_card.json.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

from analysis.model.baselines import BaseRateLoss, LogisticLoss, MarkovNext, MostFrequentNext
from analysis.model.data import Example, Story, Vocab, holdout_by_story, kfold_by_story, load_dataset, prefix_examples
from analysis.model.gru import GRUConfig, predict_examples, train_gru
from analysis.model.metrics import (binary_log_loss, calibration_table, loss_metrics, multiclass_log_loss,
                                    next_step_metrics)

MODEL_DIR = Path(__file__).resolve().parent
RESULTS_PATH = MODEL_DIR / "results.json"
MODEL_PATH = MODEL_DIR / "gru.pt"
CARD_PATH = MODEL_DIR / "model_card.json"

N_FOLDS = 5
SEED = 0
EARLY_STEPS = (1, 2, 3)
NEXT_MODELS = ("most_frequent", "markov1", "markov1_by_type", "markov2_by_type", "gru")
LOSS_MODELS = ("base_rate", "logistic", "gru")
MARKOV_VARIANTS = (("markov1", 1, False), ("markov1_by_type", 1, True), ("markov2_by_type", 2, True))
BACKOFF_GRID = (1.0, 3.0, 10.0, 30.0)   # Markov backoff strength, chosen on the validation split
L2_GRID = (1e-3, 1e-2, 1e-1)            # logistic L2 penalty, chosen on the validation split


def early_examples(stories: list[Story], k: int) -> list[Example]:
    """One example per known-outcome story: its first k steps (all of them if shorter)."""
    return [Example(s.id, s.scam_type, s.script[:k], "", s.lost) for s in stories if s.lost is not None]


def pick_by_validation(make, grid, fit_part: list[Story], val_part: list[Story], score) -> float:
    """Return the grid value whose model, fitted on fit_part, scores lowest on val_part."""
    fit_ex, val_ex = prefix_examples(fit_part), prefix_examples(val_part)
    return min(grid, key=lambda value: score(make(value).fit(fit_ex), val_ex))


def fit_models(train: list[Story], vocab: Vocab, config: GRUConfig) -> tuple[dict, dict, dict]:
    """Fit every next-step and loss model on the training stories.

    Fairness: the GRU uses a validation split (carved from the training stories) for
    early stopping, so the baselines get the same split to choose their one knob
    (Markov backoff strength, logistic L2). The test fold is never looked at.
    """
    examples = prefix_examples(train)
    fit_part, val_part = holdout_by_story(train, seed=config.seed)

    def next_log_loss(model, val_ex):
        targets = np.array([vocab.target_id(e.next_step) for e in val_ex])
        return multiclass_log_loss(model.predict_proba(val_ex), targets)

    def risk_log_loss(model, val_ex):
        known = [e for e in val_ex if e.lost is not None]
        return binary_log_loss(model.predict(known), np.array([e.lost for e in known], dtype=float))

    next_models = {"most_frequent": MostFrequentNext(vocab).fit(examples)}
    for name, order, by_type in MARKOV_VARIANTS:
        def make(b, order=order, by_type=by_type):
            return MarkovNext(vocab, order=order, by_type=by_type, backoff=b)
        backoff = pick_by_validation(make, BACKOFF_GRID, fit_part, val_part, next_log_loss)
        next_models[name] = make(backoff).fit(examples)

    l2 = pick_by_validation(lambda c: LogisticLoss(vocab, l2=c), L2_GRID, fit_part, val_part, risk_log_loss)
    loss_models = {"base_rate": BaseRateLoss().fit(examples), "logistic": LogisticLoss(vocab, l2=l2).fit(examples)}

    gru, log = train_gru(fit_part, val_part, vocab, config)
    chosen = {name: m.backoff for name, m in next_models.items() if isinstance(m, MarkovNext)} | {"logistic_l2": l2}
    return next_models, loss_models, {"model": gru, "log": log, "baseline_choices": chosen}


def predict_all(next_models, loss_models, gru, examples, vocab) -> tuple[dict, dict]:
    """Next-step probabilities and loss probabilities from every model, keyed by name."""
    gru_next, gru_loss = predict_examples(gru, examples, vocab)
    next_probs = {name: m.predict_proba(examples) for name, m in next_models.items()} | {"gru": gru_next}
    loss_probs = {name: m.predict(examples) for name, m in loss_models.items()} | {"gru": gru_loss}
    return next_probs, loss_probs


def evaluate_fold(train: list[Story], test: list[Story], vocab: Vocab, config: GRUConfig) -> tuple[dict, dict]:
    """Metrics for one fold, plus the raw loss predictions (for pooled calibration)."""
    next_models, loss_models, gru = fit_models(train, vocab, config)
    examples = prefix_examples(test)
    targets = np.array([vocab.target_id(e.next_step) for e in examples])
    lengths = np.array([len(e.prefix) for e in examples])
    next_probs, loss_probs = predict_all(next_models, loss_models, gru["model"], examples, vocab)

    known = np.array([e.lost is not None for e in examples])
    y = np.array([e.lost for e in examples if e.lost is not None], dtype=float)
    fold = {
        "n_test_stories": len(test),
        "n_test_prefixes": len(examples),
        "gru_training": gru["log"],
        "baseline_choices": gru["baseline_choices"],
        "next_step": {name: next_step_metrics(p, targets, lengths) for name, p in next_probs.items()},
        "loss_all_prefixes": {name: loss_metrics(p[known], y) for name, p in loss_probs.items()},
        "early_warning": {},
    }
    for k in EARLY_STEPS:
        early = early_examples(test, k)
        y_early = np.array([e.lost for e in early], dtype=float)
        _, early_probs = predict_all(next_models, loss_models, gru["model"], early, vocab)
        fold["early_warning"][f"first_{k}"] = {name: loss_metrics(p, y_early) for name, p in early_probs.items()}
    raw = {"y": y, **{name: p[known] for name, p in loss_probs.items()}}
    return fold, raw


def summarize(values: list) -> dict | None:
    """Mean and sample std over folds (None values skipped)."""
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"mean": float(np.mean(values)), "std": float(np.std(values, ddof=1)) if len(values) > 1 else 0.0}


def aggregate(folds: list[dict]) -> dict:
    """Mean ± std across folds for every metric of every model."""
    def collect(section: str, sub: str | None = None) -> dict:
        def get(fold):
            return fold[section][sub] if sub else fold[section]
        out = {}
        for name in get(folds[0]):
            metrics = get(folds[0])[name]
            out[name] = {m: summarize([get(f)[name][m] for f in folds]) for m in metrics}
        return out

    summary = {"next_step": collect("next_step"), "loss_all_prefixes": collect("loss_all_prefixes"),
               "early_warning": {k: collect("early_warning", k) for k in folds[0]["early_warning"]}}
    # Paired per-fold differences: same test stories, so these are fairer than comparing means.
    summary["paired_differences"] = {
        "next_top1_gru_minus_markov1_by_type": summarize(
            [f["next_step"]["gru"]["top1"] - f["next_step"]["markov1_by_type"]["top1"] for f in folds]),
        "next_log_loss_gru_minus_markov1_by_type": summarize(
            [f["next_step"]["gru"]["log_loss"] - f["next_step"]["markov1_by_type"]["log_loss"] for f in folds]),
        "loss_auc_gru_minus_logistic": summarize(
            [f["loss_all_prefixes"]["gru"]["auc"] - f["loss_all_prefixes"]["logistic"]["auc"] for f in folds]),
        "loss_brier_gru_minus_logistic": summarize(
            [f["loss_all_prefixes"]["gru"]["brier"] - f["loss_all_prefixes"]["logistic"]["brier"] for f in folds]),
    }
    return summary


def fmt(stat: dict | None, digits: int = 3) -> str:
    return "n/a" if stat is None else f"{stat['mean']:.{digits}f} ± {stat['std']:.{digits}f}"


def results_table(summary: dict) -> str:
    """Plain-text tables for the terminal (and to paste into findings.md)."""
    lines = ["Next step (all prefixes of test stories, 14 classes incl. END)",
             f"{'model':<17} {'top-1':>15} {'top-3':>15} {'log loss':>15} {'top-1 len1':>15} {'top-1 len4+':>15}"]
    for name, m in summary["next_step"].items():
        lines.append(f"{name:<17} {fmt(m['top1']):>15} {fmt(m['top3']):>15} {fmt(m['log_loss']):>15} "
                     f"{fmt(m['top1_len1']):>15} {fmt(m['top1_len4+']):>15}")
    lines += ["", "Loss risk (all prefixes of known-outcome test stories)",
              f"{'model':<17} {'ROC AUC':>15} {'Brier':>15} {'log loss':>15}"]
    for name, m in summary["loss_all_prefixes"].items():
        lines.append(f"{name:<17} {fmt(m['auc']):>15} {fmt(m['brier']):>15} {fmt(m['log_loss']):>15}")
    lines += ["", "Early warning: ROC AUC (Brier) from only the first k steps of each known-outcome story",
              f"{'model':<17} " + " ".join(f"{k:>27}" for k in summary["early_warning"])]
    for name in LOSS_MODELS:
        cells = [f"{fmt(summary['early_warning'][k][name]['auc'])} ({summary['early_warning'][k][name]['brier']['mean']:.3f})"
                 for k in summary["early_warning"]]
        lines.append(f"{name:<17} " + " ".join(f"{c:>27}" for c in cells))
    lines += ["", "Paired per-fold differences (mean ± std over folds)"]
    for name, stat in summary["paired_differences"].items():
        lines.append(f"  {name:<42} {fmt(stat)}")
    return "\n".join(lines)


def train_final(stories: list[Story], vocab: Vocab, config: GRUConfig, cv_summary: dict) -> dict:
    """Train on every story (15% held out for early stopping) and save weights + model card."""
    fit_part, val_part = holdout_by_story(stories, seed=config.seed)
    model, log = train_gru(fit_part, val_part, vocab, config)
    torch.save({"state_dict": model.state_dict(), "config": config.to_dict(),
                "steps": list(vocab.steps), "scam_types": list(vocab.scam_types),
                "targets": list(vocab.targets)}, MODEL_PATH)
    card = {
        "model": "ScamGRU (analysis/model/gru.py)",
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": {"steps": list(vocab.steps), "step_ids": "1..13 in this order, 0 = padding",
                   "scam_types": list(vocab.scam_types)},
        "next_step_targets": list(vocab.targets),
        "outputs": {"next_step_probs": "softmax over next_step_targets; END = the story stops here",
                    "p_loss": "probability the story ends with the victim losing money"},
        "excluded_from_inputs": ["money_lost", "victim_exits"],
        "hyperparameters": config.to_dict(),
        "training": {"n_stories": len(stories), "n_fit": len(fit_part), "n_early_stopping_val": len(val_part),
                     "n_known_outcome": sum(s.lost is not None for s in stories),
                     "n_lost": sum(s.lost == 1 for s in stories), **log},
        "n_parameters": sum(p.numel() for p in model.parameters()),
        "cv_metrics": {"folds": N_FOLDS, "gru_next_step": cv_summary["next_step"]["gru"],
                       "gru_loss_all_prefixes": cv_summary["loss_all_prefixes"]["gru"],
                       "gru_early_warning": {k: v["gru"] for k, v in cv_summary["early_warning"].items()}},
        "caveats": "487 self-reported r/Scams stories, LLM-extracted labels, 277 known outcomes. "
                   "The loss head sees payment steps, so part of its signal is built in. "
                   "Roughly matches simple baselines; see analysis/findings.md.",
    }
    CARD_PATH.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    return card


def main() -> None:
    torch.set_num_threads(1)   # small model: one thread is fast and keeps runs reproducible
    start = time.time()
    stories, vocab = load_dataset()
    config = GRUConfig(seed=SEED)

    folds, raws = [], []
    for i, (train, test) in enumerate(kfold_by_story(stories, k=N_FOLDS, seed=SEED)):
        fold, raw = evaluate_fold(train, test, vocab, config)
        folds.append(fold)
        raws.append(raw)
        print(f"fold {i + 1}/{N_FOLDS}: {len(train)} train / {len(test)} test stories, "
              f"GRU stopped at epoch {fold['gru_training']['best_epoch']}")

    summary = aggregate(folds)
    y = np.concatenate([r["y"] for r in raws])
    summary["calibration_pooled_out_of_fold"] = {
        name: calibration_table(np.concatenate([r[name] for r in raws]), y) for name in LOSS_MODELS}
    print()
    print(results_table(summary))

    results = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_stories": len(stories),
        "n_known_outcome": sum(s.lost is not None for s in stories),
        "n_prefixes": len(prefix_examples(stories)),
        "folds": N_FOLDS, "seed": SEED, "gru_config": config.to_dict(),
        "summary": summary, "per_fold": folds,
    }
    RESULTS_PATH.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    card = train_final(stories, vocab, config, summary)
    print(f"\nfinal model: {card['n_parameters']} parameters, best epoch {card['training']['best_epoch']}, "
          f"{MODEL_PATH.stat().st_size / 1024:.0f} KB -> {MODEL_PATH.relative_to(MODEL_DIR.parent.parent)}")
    print(f"done in {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
