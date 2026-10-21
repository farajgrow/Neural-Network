"""Turn stories into model examples, and split them by story for cross-validation.

Design choices (see analysis/README.md, "Neural network"):

- Inputs are the *script* only. The outcome steps `money_lost` and `victim_exits` are
  removed from every sequence, so the model never sees how a story ended.
- Next-step targets are the 13 script steps plus a special `END` target ("the story
  stops here"). Predicting `money_lost` / `victim_exits` as next steps would turn the
  next-step head into a second outcome predictor; `END` keeps it about the script.
- The loss label (1 = lost money, 0 = escaped) belongs to the story and is copied to
  every prefix of it. Stories with an unknown outcome get `None` and only train the
  next-step head.
- Splits are by story: all prefixes of one story land in the same fold.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from analysis.graphs import OUTCOME_STEPS, REPO_ROOT, load_stories, load_vocab, step_sequence, story_outcome

STORIES_PATH = REPO_ROOT / "data" / "stories.json"
END = "END"


@dataclass(frozen=True)
class Vocab:
    """Index tables shared by every model.

    `steps` are the script steps (inputs). Input ids are 1..len(steps); 0 is padding.
    `targets` are the next-step classes: the script steps plus END.
    """

    steps: tuple[str, ...]
    scam_types: tuple[str, ...]

    @property
    def targets(self) -> tuple[str, ...]:
        return self.steps + (END,)

    def step_id(self, step: str) -> int:
        return self.steps.index(step) + 1

    def target_id(self, target: str) -> int:
        return self.targets.index(target)

    def type_id(self, scam_type: str) -> int:
        return self.scam_types.index(scam_type)


@dataclass(frozen=True)
class Story:
    """One story reduced to what the models use."""

    id: str
    scam_type: str
    script: tuple[str, ...]   # collapsed steps, outcome steps removed
    lost: int | None          # 1 lost money, 0 escaped, None unknown


@dataclass(frozen=True)
class Example:
    """One prefix of a story and what follows it."""

    story_id: str
    scam_type: str
    prefix: tuple[str, ...]
    next_step: str            # a script step or END
    lost: int | None


def make_vocab(vocab: dict | None = None) -> Vocab:
    """Build the index tables from contract/vocab.json (outcome steps excluded)."""
    vocab = vocab or load_vocab()
    steps = tuple(s for s in vocab["steps"] if s not in OUTCOME_STEPS)
    return Vocab(steps=steps, scam_types=tuple(vocab["scam_types"]))


def script_steps(steps: list[str], vocab: Vocab) -> tuple[str, ...]:
    """Keep known script steps only and collapse repeats that removing outcomes can create."""
    script: list[str] = []
    for step in steps:
        if step in vocab.steps and (not script or script[-1] != step):
            script.append(step)
    return tuple(script)


def to_story(raw: dict, vocab: Vocab, raw_vocab: dict) -> Story:
    """Reduce a raw story dict (contract format) to a Story."""
    sequence = step_sequence(raw, frozenset(raw_vocab["steps"]))
    outcome = story_outcome(raw, frozenset(raw_vocab["outcomes"]))
    scam_type = raw.get("scam_type")
    return Story(
        id=str(raw.get("id")),
        scam_type=scam_type if scam_type in vocab.scam_types else "other",
        script=script_steps(sequence, vocab),
        lost={"lost_money": 1, "escaped": 0}.get(outcome),
    )


def load_dataset(path: Path = STORIES_PATH) -> tuple[list[Story], Vocab]:
    """Load data/stories.json as Story objects, dropping stories with no script steps."""
    raw_vocab = load_vocab()
    vocab = make_vocab(raw_vocab)
    stories = [to_story(raw, vocab, raw_vocab) for raw in load_stories(path)]
    return [s for s in stories if s.script], vocab


def prefix_examples(stories: list[Story]) -> list[Example]:
    """Every prefix of length >= 1 of each story, labelled with the step after it (or END)."""
    examples = []
    for story in stories:
        for k in range(1, len(story.script) + 1):
            nxt = story.script[k] if k < len(story.script) else END
            examples.append(Example(story.id, story.scam_type, story.script[:k], nxt, story.lost))
    return examples


def kfold_by_story(stories: list[Story], k: int = 5, seed: int = 0) -> list[tuple[list[Story], list[Story]]]:
    """Split stories into k (train, test) folds, never putting one story on both sides.

    Stratified by scam type: each type's stories are shuffled with `seed` and dealt
    round-robin to the folds, so every fold has a similar type mix.
    """
    rng = random.Random(seed)
    fold_of: dict[str, int] = {}
    by_type: dict[str, list[Story]] = defaultdict(list)
    for story in sorted(stories, key=lambda s: s.id):
        by_type[story.scam_type].append(story)
    offset = 0
    for scam_type in sorted(by_type):
        group = by_type[scam_type]
        rng.shuffle(group)
        for i, story in enumerate(group):
            fold_of[story.id] = (offset + i) % k
        offset += len(group)   # keeps fold sizes balanced across types
    return [
        ([s for s in stories if fold_of[s.id] != f], [s for s in stories if fold_of[s.id] == f])
        for f in range(k)
    ]


def holdout_by_story(stories: list[Story], share: float = 0.15, seed: int = 0) -> tuple[list[Story], list[Story]]:
    """Carve a validation set (for early stopping) out of the training stories."""
    shuffled = sorted(stories, key=lambda s: s.id)
    random.Random(seed).shuffle(shuffled)
    n_val = max(1, round(share * len(shuffled)))
    return shuffled[n_val:], shuffled[:n_val]
