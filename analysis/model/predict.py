"""Run the saved GRU on a partial story.

    from analysis.model.predict import predict
    predict(["initial_contact", "platform_switch"], "pig_butchering")
    # {"next_step_probs": {"trust_building": 0.31, ..., "withdrawal_blocked": 0.02},
    #  "top_next": "trust_building", "p_loss": 0.18}

`next_step_probs` covers the scammer's moves only (11 steps, no reactions, outcomes or
END) and sums to 1: "if the scammer makes another move, which one is it?". It may still
give weight to a move already seen (scripts can repeat a move after a reaction).
Outcome steps in the input (`money_lost`, `victim_exits`) and unknown steps are ignored,
matching how the model was trained; reactions such as `victim_suspicious` are used.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import torch

from analysis.model.data import Example, Vocab, script_steps
from analysis.model.gru import GRUConfig, ScamGRU, predict_examples

MODEL_PATH = Path(__file__).resolve().parent / "gru.pt"


@lru_cache(maxsize=4)
def load_model(path: Path = MODEL_PATH) -> tuple[ScamGRU, Vocab]:
    """Load the saved weights and the vocabulary they were trained with (cached)."""
    saved = torch.load(path, map_location="cpu", weights_only=True)
    vocab = Vocab(steps=tuple(saved["steps"]), scam_types=tuple(saved["scam_types"]))
    if tuple(saved.get("targets", ())) != vocab.targets:
        raise ValueError(f"{path} was trained with different next-step targets; retrain with analysis.model.train")
    model = ScamGRU(len(vocab.steps), len(vocab.scam_types), len(vocab.targets), GRUConfig(**saved["config"]))
    model.load_state_dict(saved["state_dict"])
    model.eval()
    return model, vocab


def predict(steps: list[str], scam_type: str, path: Path = MODEL_PATH) -> dict:
    """Next-step probabilities and loss probability for the steps seen so far.

    Raises ValueError if no input step is left after filtering. An unknown scam_type
    is treated as "other".
    """
    model, vocab = load_model(path)
    prefix = script_steps(steps, vocab)
    if not prefix:
        raise ValueError("predict() needs at least one non-outcome step (outcome steps are ignored)")
    if scam_type not in vocab.scam_types:
        scam_type = "other"
    next_probs, loss_probs = predict_examples(model, [Example("query", scam_type, prefix, "", None)], vocab)
    probs = {target: float(p) for target, p in zip(vocab.targets, next_probs[0])}
    return {
        "next_step_probs": probs,
        "top_next": max(probs, key=probs.get),
        "p_loss": float(loss_probs[0]),
    }
