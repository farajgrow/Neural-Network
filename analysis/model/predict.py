"""Run the saved GRU on a partial story.

    from analysis.model.predict import predict
    predict(["initial_contact", "platform_switch"], "pig_butchering")
    # {"next_step_probs": {"trust_building": 0.21, ..., "END": 0.05},
    #  "top_next": "trust_building", "p_loss": 0.18}

`END` in next_step_probs means "the story stops here" (the victim exits, loses money,
or the post ends). Outcome steps in the input (`money_lost`, `victim_exits`) and unknown
steps are ignored, matching how the model was trained.
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
    model = ScamGRU(len(vocab.steps), len(vocab.scam_types), len(vocab.targets), GRUConfig(**saved["config"]))
    model.load_state_dict(saved["state_dict"])
    model.eval()
    return model, vocab


def predict(steps: list[str], scam_type: str, path: Path = MODEL_PATH) -> dict:
    """Next-step probabilities and loss probability for the steps seen so far.

    Raises ValueError if no script step is left after filtering. An unknown scam_type
    is treated as "other".
    """
    model, vocab = load_model(path)
    prefix = script_steps(steps, vocab)
    if not prefix:
        raise ValueError("predict() needs at least one script step (outcome steps are ignored)")
    if scam_type not in vocab.scam_types:
        scam_type = "other"
    next_probs, loss_probs = predict_examples(model, [Example("query", scam_type, prefix, "", None)], vocab)
    probs = {target: float(p) for target, p in zip(vocab.targets, next_probs[0])}
    return {
        "next_step_probs": probs,
        "top_next": max(probs, key=probs.get),
        "p_loss": float(loss_probs[0]),
    }
