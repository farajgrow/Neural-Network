"""A small GRU with two heads: the next step, and the chance the story ends in a loss.

    step embedding (16) ┐
                        ├─ concat ─> GRU (1 layer, 32) ─> dropout ─┬─> next-move logits (11)
    scam-type emb. (4)  ┘                                          └─> loss logit (1)

The GRU reads left to right, so its output at position t only depends on steps 1..t.
That means one pass over a whole story gives the prediction for every prefix of it at
once: training on stories is the same as training on all their prefixes, just faster.
"""

from __future__ import annotations

import copy
import random
from dataclasses import asdict, dataclass

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from analysis.model.data import Example, Story, Vocab, next_moves


@dataclass
class GRUConfig:
    """Hyperparameters. Set once up front, not tuned on the test folds."""

    step_dim: int = 16
    type_dim: int = 4
    hidden: int = 32
    dropout: float = 0.3
    lr: float = 5e-3
    weight_decay: float = 1e-3
    batch_size: int = 32
    max_epochs: int = 200
    patience: int = 15          # early stopping: epochs without validation improvement
    loss_weight: float = 1.0    # weight of the loss-risk head in the training objective
    seed: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class ScamGRU(nn.Module):
    """Embeddings -> GRU -> (next-step logits, loss logit) at every position."""

    def __init__(self, n_steps: int, n_types: int, n_targets: int, config: GRUConfig):
        super().__init__()
        self.step_emb = nn.Embedding(n_steps + 1, config.step_dim, padding_idx=0)
        self.type_emb = nn.Embedding(n_types, config.type_dim)
        self.gru = nn.GRU(config.step_dim + config.type_dim, config.hidden, batch_first=True)
        self.dropout = nn.Dropout(config.dropout)
        self.next_head = nn.Linear(config.hidden, n_targets)
        self.loss_head = nn.Linear(config.hidden, 1)

    def forward(self, steps: torch.Tensor, types: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """steps: (batch, time) ids, 0 = padding. types: (batch,).

        Returns next-step logits (batch, time, n_targets) and loss logits (batch, time).
        Outputs at padded positions are meaningless and must be masked by the caller.
        """
        type_vec = self.type_emb(types).unsqueeze(1).expand(-1, steps.shape[1], -1)
        x = torch.cat([self.step_emb(steps), type_vec], dim=-1)
        h, _ = self.gru(self.dropout(x))
        h = self.dropout(h)
        return self.next_head(h), self.loss_head(h).squeeze(-1)


def encode_stories(stories: list[Story], vocab: Vocab) -> dict[str, torch.Tensor]:
    """Pad stories into tensors. Position t holds step t and the next move after it.

    `mask` marks real positions; `next_mask` marks real positions that have a later move
    (the next-step head's examples); `loss_mask` marks real positions of known-outcome
    stories (the loss head's examples, including positions with no later move).
    """
    n, t = len(stories), max(len(s.script) for s in stories)
    steps = torch.zeros(n, t, dtype=torch.long)
    targets = torch.zeros(n, t, dtype=torch.long)
    mask = torch.zeros(n, t, dtype=torch.bool)
    next_mask = torch.zeros(n, t, dtype=torch.bool)
    lost = torch.zeros(n, t)
    loss_mask = torch.zeros(n, t, dtype=torch.bool)
    for i, story in enumerate(stories):
        length = len(story.script)
        steps[i, :length] = torch.tensor([vocab.step_id(s) for s in story.script])
        for k, nxt in enumerate(next_moves(story.script)):
            if nxt is not None:
                targets[i, k] = vocab.target_id(nxt)
                next_mask[i, k] = True
        mask[i, :length] = True
        if story.lost is not None:
            lost[i, :length] = float(story.lost)
            loss_mask[i, :length] = True
    types = torch.tensor([vocab.type_id(s.scam_type) for s in stories])
    return {"steps": steps, "types": types, "targets": targets, "mask": mask, "next_mask": next_mask,
            "lost": lost, "loss_mask": loss_mask}


def encode_prefixes(prefixes: list[tuple[str, ...]], scam_types: list[str], vocab: Vocab) -> dict[str, torch.Tensor]:
    """Pad a batch of prefixes (inputs only) for prediction."""
    t = max(len(p) for p in prefixes)
    steps = torch.zeros(len(prefixes), t, dtype=torch.long)
    for i, prefix in enumerate(prefixes):
        steps[i, :len(prefix)] = torch.tensor([vocab.step_id(s) for s in prefix])
    return {"steps": steps, "types": torch.tensor([vocab.type_id(t) for t in scam_types]),
            "lengths": torch.tensor([len(p) for p in prefixes])}


def masked_losses(next_logits, loss_logits, batch) -> tuple[torch.Tensor, torch.Tensor]:
    """Mean cross-entropy over positions with a later move, and mean BCE over known-outcome positions.

    If the batch has no known outcomes the loss-head term is 0, so unknown-outcome
    stories never push the loss head in either direction. Likewise the next-step term
    is 0 for a batch with no position that has a later move.
    """
    next_mask = batch["next_mask"]
    if next_mask.any():
        next_loss = F.cross_entropy(next_logits[next_mask], batch["targets"][next_mask])
    else:
        next_loss = next_logits.sum() * 0.0
    loss_mask = batch["loss_mask"]
    if loss_mask.any():
        risk_loss = F.binary_cross_entropy_with_logits(loss_logits[loss_mask], batch["lost"][loss_mask])
    else:
        risk_loss = loss_logits.sum() * 0.0
    return next_loss, risk_loss


def set_seed(seed: int) -> None:
    """Seed every random number generator the training uses."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def _subset(batch: dict, index: torch.Tensor) -> dict:
    return {k: v[index] for k, v in batch.items()}


def train_gru(train: list[Story], val: list[Story], vocab: Vocab, config: GRUConfig) -> tuple[ScamGRU, dict]:
    """Train with Adam + weight decay; keep the weights with the best validation objective.

    Returns the model (in eval mode) and a small training log.
    """
    set_seed(config.seed)
    model = ScamGRU(len(vocab.steps), len(vocab.scam_types), len(vocab.targets), config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.lr, weight_decay=config.weight_decay)
    train_batch, val_batch = encode_stories(train, vocab), encode_stories(val, vocab)
    generator = torch.Generator().manual_seed(config.seed)

    best = (float("inf"), 0, copy.deepcopy(model.state_dict()))
    for epoch in range(1, config.max_epochs + 1):
        model.train()
        for index in torch.randperm(len(train), generator=generator).split(config.batch_size):
            batch = _subset(train_batch, index)
            next_loss, risk_loss = masked_losses(*model(batch["steps"], batch["types"]), batch)
            optimizer.zero_grad()
            (next_loss + config.loss_weight * risk_loss).backward()
            optimizer.step()

        model.eval()
        with torch.no_grad():
            next_loss, risk_loss = masked_losses(*model(val_batch["steps"], val_batch["types"]), val_batch)
            objective = float(next_loss + config.loss_weight * risk_loss)
        if objective < best[0] - 1e-4:
            best = (objective, epoch, copy.deepcopy(model.state_dict()))
        elif epoch - best[1] >= config.patience:
            break

    model.load_state_dict(best[2])
    model.eval()
    return model, {"best_epoch": best[1], "best_val_objective": best[0], "epochs_run": epoch}


@torch.no_grad()
def predict_examples(model: ScamGRU, examples: list[Example], vocab: Vocab) -> tuple[np.ndarray, np.ndarray]:
    """Next-step probabilities (n, n_targets) and loss probabilities (n,) for prefix examples."""
    batch = encode_prefixes([e.prefix for e in examples], [e.scam_type for e in examples], vocab)
    model.eval()
    next_logits, loss_logits = model(batch["steps"], batch["types"])
    last = batch["lengths"] - 1          # read the output at each prefix's final step
    rows = torch.arange(len(examples))
    next_probs = torch.softmax(next_logits[rows, last], dim=-1)
    loss_probs = torch.sigmoid(loss_logits[rows, last])
    return next_probs.numpy().astype(float), loss_probs.numpy().astype(float)
