"""Turn stories into model examples, and split them by story for cross-validation.

Design choices (see analysis/README.md, "Neural network"):

- Inputs are every step except the outcome steps `money_lost` and `victim_exits`, which
  are removed from every sequence so the model never sees how a story ended. The victim
  reactions `victim_suspicious` and `third_party_intervened` stay in the inputs on
  purpose: they say a lot about what happens next and about loss risk.
- Next-step targets (v2) are the scammer's MOVES only: the vocabulary minus the
  reactions {victim_suspicious, third_party_intervened, victim_exits} and minus
  `money_lost` (11 steps). For a prefix, the target is the next move that comes later in
  the story, skipping any reactions in between. If no move follows, the prefix has no
  next-step target (`next_step is None`). There is no `END` target.
  (v1 predicted the very next step or END, and was dominated by `victim_suspicious`,
  which is why people post at all; that is useless as a warning of the scammer's plan.)
- The loss label (1 = lost money, 0 = escaped) belongs to the story and is copied to
  every prefix of it, including prefixes with no later move. Stories with an unknown
  outcome get `None` and only train the next-step head.
- So the two heads use different example sets: `next_step_examples` (prefixes with a
  later move) and `loss_examples` (every prefix of a known-outcome story).
- Splits are by story: all prefixes of one story land in the same fold.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from analysis.graphs import OUTCOME_STEPS, REPO_ROOT, load_stories, load_vocab, step_sequence, story_outcome

STORIES_PATH = REPO_ROOT / "data" / "stories.json"
# Same rule as pipeline/matcher.py (SCRIPT_STEPS minus money_lost), defined here on purpose.
REACTION_STEPS = frozenset({"victim_suspicious", "third_party_intervened", "victim_exits"})
NOT_A_MOVE = REACTION_STEPS | OUTCOME_STEPS


@dataclass(frozen=True)
class Vocab:
    """Index tables shared by every model.

    `steps` are the input steps (outcomes removed). Input ids are 1..len(steps); 0 is padding.
    `targets` are the next-step classes: the scammer's moves (inputs minus reactions).
    """

    steps: tuple[str, ...]
    scam_types: tuple[str, ...]

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(s for s in self.steps if s not in NOT_A_MOVE)

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
    script: tuple[str, ...]   # collapsed steps, outcome steps removed (reactions kept)
    lost: int | None          # 1 lost money, 0 escaped, None unknown


@dataclass(frozen=True)
class Example:
    """One prefix of a story and what follows it."""

    story_id: str
    scam_type: str
    prefix: tuple[str, ...]
    next_step: str | None     # the next scammer move later in the story, None if there is none
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


def is_move(step: str) -> bool:
    """True for a scammer move (not a victim reaction or an outcome)."""
    return step not in NOT_A_MOVE


def next_moves(script: tuple[str, ...]) -> list[str | None]:
    """For each position k, the first move strictly after it (reactions skipped), or None."""
    out: list[str | None] = [None] * len(script)
    upcoming = None
    for k in range(len(script) - 1, -1, -1):
        out[k] = upcoming
        if is_move(script[k]):
            upcoming = script[k]
    return out


def prefix_examples(stories: list[Story]) -> list[Example]:
    """Every prefix of length >= 1 of each story, labelled with the next move after it.

    `next_step` is None when no move follows. Use `next_step_examples` / `loss_examples`
    to get the example set for each head.
    """
    examples = []
    for story in stories:
        for k, nxt in enumerate(next_moves(story.script), start=1):
            examples.append(Example(story.id, story.scam_type, story.script[:k], nxt, story.lost))
    return examples


def next_step_examples(stories: list[Story]) -> list[Example]:
    """Prefixes that have a later move: the next-step head's examples."""
    return [e for e in prefix_examples(stories) if e.next_step is not None]


def loss_examples(stories: list[Story]) -> list[Example]:
    """Every prefix of a known-outcome story: the loss head's examples (same set as v1)."""
    return [e for e in prefix_examples(stories) if e.lost is not None]


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
