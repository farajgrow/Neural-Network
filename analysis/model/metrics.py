"""Evaluation metrics, in plain numpy so every number is easy to check by hand."""

from __future__ import annotations

import numpy as np

EPS = 1e-12


def topk_accuracy(probs: np.ndarray, targets: np.ndarray, k: int) -> float:
    """Share of rows whose true class is among the k highest probabilities.

    Ties are broken by class index (lower first), the same way argmax does.
    """
    order = np.argsort(-probs, axis=1, kind="stable")[:, :k]
    return float(np.mean([t in row for t, row in zip(targets, order)]))


def multiclass_log_loss(probs: np.ndarray, targets: np.ndarray) -> float:
    """Mean negative log probability of the true class."""
    return float(-np.mean(np.log(np.clip(probs[np.arange(len(targets)), targets], EPS, 1.0))))


def binary_log_loss(p: np.ndarray, y: np.ndarray) -> float:
    """Mean negative log likelihood for binary labels."""
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(p: np.ndarray, y: np.ndarray) -> float:
    """Mean squared error between predicted probability and the 0/1 label."""
    return float(np.mean((p - y) ** 2))


def roc_auc(p: np.ndarray, y: np.ndarray) -> float | None:
    """Probability that a random positive scores above a random negative (ties count half).

    Computed with average ranks (the Mann-Whitney U statistic). None if only one class.
    """
    y = np.asarray(y)
    n_pos, n_neg = int(y.sum()), int(len(y) - y.sum())
    if n_pos == 0 or n_neg == 0:
        return None
    order = np.argsort(p, kind="stable")
    sorted_p = np.asarray(p)[order]
    ranks = np.empty(len(p))
    i = 0
    while i < len(p):   # average rank over each run of tied scores
        j = i
        while j + 1 < len(p) and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def calibration_table(p: np.ndarray, y: np.ndarray, bins: int = 5) -> list[dict]:
    """Equal-width bins over [0, 1]: mean predicted vs observed loss rate per bin."""
    edges = np.linspace(0, 1, bins + 1)
    index = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    table = []
    for b in range(bins):
        mask = index == b
        table.append({
            "bin": f"{edges[b]:.1f}-{edges[b + 1]:.1f}",
            "n": int(mask.sum()),
            "predicted": float(p[mask].mean()) if mask.any() else None,
            "observed": float(y[mask].mean()) if mask.any() else None,
        })
    return table


def top1_unseen(probs: np.ndarray, targets: np.ndarray, seen: np.ndarray) -> float:
    """Top-1 accuracy after removing classes already seen in the prefix (what the app shows).

    `seen` is a boolean (n, n_classes) mask. A row whose true class was already seen counts
    as a miss. Rows where every class was seen also count as a miss.
    """
    masked = np.where(seen, -1.0, probs)
    hits = [(not row_seen.all()) and int(np.argmax(row)) == t
            for row, row_seen, t in zip(masked, seen, targets)]
    return float(np.mean(hits))


def next_step_metrics(probs: np.ndarray, targets: np.ndarray, prefix_lengths: np.ndarray,
                      seen: np.ndarray | None = None) -> dict:
    """Top-1 / top-3 accuracy and log loss, plus top-1 accuracy by prefix length (1, 2, 3, 4+).

    With `seen` (boolean mask of classes already in the prefix), also top-1 among unseen classes.
    """
    out = {
        "top1": topk_accuracy(probs, targets, 1),
        "top3": topk_accuracy(probs, targets, 3),
        "log_loss": multiclass_log_loss(probs, targets),
    }
    if seen is not None:
        out["top1_unseen"] = top1_unseen(probs, targets, seen)
    buckets = np.minimum(prefix_lengths, 4)
    for b in (1, 2, 3, 4):
        mask = buckets == b
        name = f"top1_len{b}" + ("+" if b == 4 else "")
        out[name] = topk_accuracy(probs[mask], targets[mask], 1) if mask.any() else None
    return out


def loss_metrics(p: np.ndarray, y: np.ndarray) -> dict:
    """AUC, Brier score and log loss for the loss-risk head."""
    return {"auc": roc_auc(p, y), "brier": brier(p, y), "log_loss": binary_log_loss(p, y), "n": int(len(y))}
