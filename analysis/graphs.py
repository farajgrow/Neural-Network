"""Build transition graphs, pinch points and escape points from extracted stories."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VOCAB_PATH = REPO_ROOT / "contract" / "vocab.json"

OUTCOME_STEPS = frozenset({"money_lost", "victim_exits"})
EXIT_STEP = "victim_exits"
TOP_N = 3
PRECISION = 4
MIN_SUPPORT_ABS = 3        # pinch/escape candidates need count >= max(3, 5% of the group's stories)
MIN_SUPPORT_SHARE = 0.05
MIN_EXIT_COUNT = 2         # an escape point needs at least this many exits
PRIOR_WEIGHT = 5           # pseudo-stories used to shrink loss rates when ranking pinch points


def min_support(n_stories: int) -> int:
    """Minimum node count for a step to be a pinch or escape point candidate."""
    return max(MIN_SUPPORT_ABS, math.ceil(MIN_SUPPORT_SHARE * n_stories))


def load_vocab(path: Path = VOCAB_PATH) -> dict:
    """Load the shared vocabulary (steps, scam types, outcomes, ...)."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_stories(path: Path) -> list[dict]:
    """Load the stories file, keeping only entries that are objects."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array of stories")
    return [s for s in data if isinstance(s, dict)]


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, PRECISION)


def step_sequence(story: dict, valid_steps: frozenset[str]) -> list[str]:
    """Return the story's valid steps in order, with consecutive repeats collapsed."""
    events = story.get("events")
    if not isinstance(events, list):
        return []
    sequence: list[str] = []
    for event in events:
        step = event.get("step") if isinstance(event, dict) else None
        if step not in valid_steps:
            continue
        if sequence and sequence[-1] == step:
            continue
        sequence.append(step)
    return sequence


def story_outcome(story: dict, valid_outcomes: frozenset[str]) -> str:
    """Return the story's outcome, treating anything unrecognised as unknown."""
    outcome = story.get("outcome")
    return outcome if outcome in valid_outcomes else "unknown"


def loss_rate(outcomes: list[str]) -> float | None:
    """Share of known-outcome stories that lost money, or None if none are known."""
    known = [o for o in outcomes if o != "unknown"]
    if not known:
        return None
    return sum(o == "lost_money" for o in known) / len(known)


def _shrunk(node: dict, prior: float, weight: float) -> float:
    """Loss rate pulled toward `prior` as if `weight` extra stories had the prior rate."""
    return (node["n_lost"] + weight * prior) / (node["n_known"] + weight)


def pinch_points(nodes: dict[str, dict], edges: Counter, support: int = 1,
                 prior: float | None = None, prior_weight: float = PRIOR_WEIGHT) -> list[dict]:
    """Steps whose loss rate jumps most above the edge-weighted mean of their predecessors.

    Candidates are steps seen in at least `support` stories with at least MIN_SUPPORT_ABS
    known outcomes. `jump` is the raw difference; points are ranked by `score`, the same
    difference after shrinking every loss rate toward `prior` (the group's loss rate) with
    `prior_weight` pseudo-stories, so a 3-for-3 step does not beat a 40-of-45 one.
    """
    incoming: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (src, dst), count in edges.items():
        incoming[dst].append((src, count))
    if prior is None:
        known = sum(n["n_known"] for n in nodes.values())
        prior = sum(n["n_lost"] for n in nodes.values()) / known if known else 0.0

    points = []
    for step, node in nodes.items():
        if (step in OUTCOME_STEPS or node["loss_rate"] is None or node["count"] < support
                or node["n_known"] < MIN_SUPPORT_ABS):
            continue
        weighted = [
            (nodes[src]["loss_rate"], _shrunk(nodes[src], prior, prior_weight), count)
            for src, count in incoming.get(step, [])
            if nodes[src]["loss_rate"] is not None
        ]
        total = sum(count for _, _, count in weighted)
        if not total:
            continue
        prev = sum(rate * count for rate, _, count in weighted) / total
        prev_shrunk = sum(rate * count for _, rate, count in weighted) / total
        jump = node["loss_rate"] - prev
        score = _shrunk(node, prior, prior_weight) - prev_shrunk
        if jump > 1e-9 and score > 1e-9:
            points.append({
                "step": step,
                "loss_rate": _round(node["loss_rate"]),
                "prev_loss_rate": _round(prev),
                "jump": _round(jump),
                "n_known": node["n_known"],
                "score": _round(score),
            })
    points.sort(key=lambda p: (-p["score"], -p["jump"], p["step"]))
    return points[:TOP_N]


def escape_points(nodes: dict[str, dict], edges: Counter, support: int = 1) -> list[dict]:
    """Steps most often followed directly by victim_exits.

    Candidates are non-outcome steps seen in >= `support` stories with at least
    MIN_EXIT_COUNT exits.
    """
    points = []
    for (src, dst), count in edges.items():
        if (dst != EXIT_STEP or src in OUTCOME_STEPS or nodes[src]["count"] < support
                or count < MIN_EXIT_COUNT):
            continue
        points.append({
            "step": src,
            "exit_count": count,
            "exit_rate": _round(count / nodes[src]["count"]),
        })
    points.sort(key=lambda p: (-p["exit_count"], -p["exit_rate"], p["step"]))
    return points[:TOP_N]


def build_graph(stories: list[dict], scam_type: str, vocab: dict) -> dict:
    """Build the graph dict for one group of stories."""
    valid_steps = frozenset(vocab["steps"])
    valid_outcomes = frozenset(vocab["outcomes"])

    outcomes: list[str] = []
    step_outcomes: dict[str, list[str]] = defaultdict(list)
    step_positions: dict[str, list[float]] = defaultdict(list)
    step_ids: dict[str, list[str]] = defaultdict(list)
    edges: Counter = Counter()
    edge_outcomes: dict[tuple[str, str], list[str]] = defaultdict(list)

    for story in stories:
        outcome = story_outcome(story, valid_outcomes)
        outcomes.append(outcome)
        sequence = step_sequence(story, valid_steps)
        last = max(len(sequence) - 1, 1)

        first_index: dict[str, int] = {}
        for index, step in enumerate(sequence):
            first_index.setdefault(step, index)
        for step, index in first_index.items():
            step_outcomes[step].append(outcome)
            step_positions[step].append(index / last)
            story_id = story.get("id")
            if isinstance(story_id, str):
                step_ids[step].append(story_id)

        edges.update(zip(sequence, sequence[1:]))
        for edge in set(zip(sequence, sequence[1:])):
            edge_outcomes[edge].append(outcome)

    nodes = {
        step: {
            "step": step,
            "count": len(step_outcomes[step]),
            "loss_rate": loss_rate(step_outcomes[step]),
            "n_known": sum(o != "unknown" for o in step_outcomes[step]),
            "n_lost": sum(o == "lost_money" for o in step_outcomes[step]),
            "avg_position": sum(step_positions[step]) / len(step_positions[step]),
        }
        for step in vocab["steps"]
        if step in step_outcomes
    }

    counts = Counter(outcomes)
    support = min_support(len(stories))
    return {
        "scam_type": scam_type,
        "n_stories": len(stories),
        "loss_rate": _round(loss_rate(outcomes)),
        "outcome_counts": {o: counts.get(o, 0) for o in vocab["outcomes"]},
        "nodes": [
            {**{k: v for k, v in node.items() if k != "n_lost"},
             "loss_rate": _round(node["loss_rate"]), "avg_position": _round(node["avg_position"])}
            for node in sorted(nodes.values(), key=lambda n: (n["avg_position"], n["step"]))
        ],
        "edges": [
            {"from": src, "to": dst, "count": count,
             "loss_rate": _round(loss_rate(edge_outcomes[(src, dst)])),
             "n_known": sum(o != "unknown" for o in edge_outcomes[(src, dst)])}
            for (src, dst), count in sorted(edges.items(), key=lambda e: (-e[1], e[0]))
        ],
        "min_support": support,
        "pinch_points": pinch_points(nodes, edges, support, loss_rate(outcomes) or 0.0),
        "escape_points": escape_points(nodes, edges, support),
        "step_to_story_ids": {step: sorted(step_ids[step]) for step in nodes if step in step_ids},
    }


def build_graphs_file(stories: list[dict], vocab: dict | None = None) -> dict:
    """Build the full graphs file: one graph per scam type plus "all"."""
    vocab = vocab or load_vocab()
    valid_types = vocab["scam_types"]

    by_type: dict[str, list[dict]] = defaultdict(list)
    for story in stories:
        scam_type = story.get("scam_type")
        by_type[scam_type if scam_type in valid_types else "other"].append(story)

    scam_types = sorted(by_type, key=lambda t: (-len(by_type[t]), valid_types.index(t)))
    graphs = {t: build_graph(by_type[t], t, vocab) for t in scam_types}
    graphs["all"] = build_graph(stories, "all", vocab)

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_stories": len(stories),
        "scam_types": scam_types,
        "graphs": graphs,
        "clusters": None,
    }
