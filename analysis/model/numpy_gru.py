"""ScamGRU's forward pass in pure numpy, for inference where torch is not available.

Reads gru_weights.npz (written by analysis.model.export_weights) and reproduces
analysis.model.gru.ScamGRU in eval mode (dropout is the identity):

    x_t = [step_emb[step_t], type_emb[scam_type]]
    r = sigmoid(W_ir x + b_ir + W_hr h + b_hr)
    z = sigmoid(W_iz x + b_iz + W_hz h + b_hz)
    n = tanh(W_in x + b_in + r * (W_hn h + b_hn))
    h' = (1 - z) * n + z * h                     (PyTorch's gate order r, z, n; h_0 = 0)

The heads are read at the last step: softmax(next_head(h)) and sigmoid(loss_head(h)).
No torch import here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from analysis.model.data import Vocab

WEIGHTS_PATH = Path(__file__).resolve().parent / "gru_weights.npz"


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.tanh(0.5 * x))      # numerically stable for large |x|


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max())
    return e / e.sum()


@dataclass(frozen=True)
class NumpyGRU:
    """The trained weights (float64) and the vocabulary they were trained with."""

    vocab: Vocab
    config: dict
    step_emb: np.ndarray     # (n_steps + 1, step_dim), row 0 = padding
    type_emb: np.ndarray     # (n_types, type_dim)
    w_ih: np.ndarray         # (3 * hidden, step_dim + type_dim), rows r | z | n
    w_hh: np.ndarray         # (3 * hidden, hidden)
    b_ih: np.ndarray         # (3 * hidden,)
    b_hh: np.ndarray         # (3 * hidden,)
    next_w: np.ndarray       # (n_targets, hidden)
    next_b: np.ndarray       # (n_targets,)
    loss_w: np.ndarray       # (1, hidden)
    loss_b: np.ndarray       # (1,)

    def hidden_state(self, prefix: tuple[str, ...], scam_type: str) -> np.ndarray:
        """GRU hidden state after reading every step of the prefix."""
        type_vec = self.type_emb[self.vocab.type_id(scam_type)]
        hidden = self.w_hh.shape[1]
        h = np.zeros(hidden)
        for step in prefix:
            x = np.concatenate([self.step_emb[self.vocab.step_id(step)], type_vec])
            gi = self.w_ih @ x + self.b_ih
            gh = self.w_hh @ h + self.b_hh
            r = _sigmoid(gi[:hidden] + gh[:hidden])
            z = _sigmoid(gi[hidden:2 * hidden] + gh[hidden:2 * hidden])
            n = np.tanh(gi[2 * hidden:] + r * gh[2 * hidden:])
            h = (1.0 - z) * n + z * h
        return h

    def predict_prefix(self, prefix: tuple[str, ...], scam_type: str) -> tuple[np.ndarray, float]:
        """Next-step probabilities (n_targets,) and loss probability for one non-empty prefix."""
        if not prefix:
            raise ValueError("predict_prefix() needs at least one step")
        h = self.hidden_state(prefix, scam_type)
        next_probs = _softmax(self.next_w @ h + self.next_b)
        p_loss = float(_sigmoid(self.loss_w @ h + self.loss_b)[0])
        return next_probs, p_loss


@lru_cache(maxsize=4)
def load_numpy_model(path: Path = WEIGHTS_PATH) -> NumpyGRU:
    """Load gru_weights.npz (cached)."""
    with np.load(path, allow_pickle=False) as f:
        w = {k: f[k] for k in f.files}
    vocab = Vocab(steps=tuple(str(s) for s in w["meta.steps"]),
                  scam_types=tuple(str(s) for s in w["meta.scam_types"]))
    if tuple(str(s) for s in w["meta.targets"]) != vocab.targets:
        raise ValueError(f"{path} was trained with different next-step targets; retrain with analysis.model.train")

    def get(name: str) -> np.ndarray:
        return w[name].astype(np.float64)

    return NumpyGRU(
        vocab=vocab,
        config=json.loads(str(w["meta.config"])),
        step_emb=get("step_emb.weight"),
        type_emb=get("type_emb.weight"),
        w_ih=get("gru.weight_ih_l0"),
        w_hh=get("gru.weight_hh_l0"),
        b_ih=get("gru.bias_ih_l0"),
        b_hh=get("gru.bias_hh_l0"),
        next_w=get("next_head.weight"),
        next_b=get("next_head.bias"),
        loss_w=get("loss_head.weight"),
        loss_b=get("loss_head.bias"),
    )
