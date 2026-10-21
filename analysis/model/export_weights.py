"""Export gru.pt to gru_weights.npz, so inference can run on numpy alone (no torch).

Usage (from the repo root):  python -m analysis.model.export_weights

train.py calls `export()` right after saving gru.pt, so the two files never drift.
The .npz holds every tensor of the state dict under its own name (float32), plus the
vocabulary (`meta.steps`, `meta.scam_types`, `meta.targets`) and `meta.config` (JSON).
It loads with `np.load(path, allow_pickle=False)`.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

MODEL_DIR = Path(__file__).resolve().parent
MODEL_PATH = MODEL_DIR / "gru.pt"
WEIGHTS_PATH = MODEL_DIR / "gru_weights.npz"


def export(saved: dict, out: Path = WEIGHTS_PATH) -> Path:
    """Write the dict that train.py saves as gru.pt (state_dict, config, vocab) to an .npz."""
    arrays = {name: tensor.detach().cpu().numpy().astype(np.float32)
              for name, tensor in saved["state_dict"].items()}
    arrays["meta.steps"] = np.array(list(saved["steps"]), dtype=str)
    arrays["meta.scam_types"] = np.array(list(saved["scam_types"]), dtype=str)
    arrays["meta.targets"] = np.array(list(saved["targets"]), dtype=str)
    arrays["meta.config"] = np.array(json.dumps(saved["config"], sort_keys=True))
    np.savez_compressed(out, **arrays)
    return out


def main(src: Path = MODEL_PATH, out: Path = WEIGHTS_PATH) -> None:
    import torch

    saved = torch.load(src, map_location="cpu", weights_only=True)
    path = export(saved, out)
    print(f"wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
