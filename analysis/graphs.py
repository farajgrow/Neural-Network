"""Build transition graphs, pinch points and escape points from extracted stories."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VOCAB_PATH = REPO_ROOT / "contract" / "vocab.json"

OUTCOME_STEPS = frozenset({"money_lost", "victim_exits"})
EXIT_STEP = "victim_exits"
TOP_N = 3
PRECISION = 4


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


def pinch_points(nodes: dict[str, dict], edges: Counter) -> list[dict]:
    """Steps whose loss rate jumps most above the edge-weighted mean of their predecessors."""
    incoming: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (src, dst), count in edges.items():
        incoming[dst].append((src, count))

    points = []
    for step, node in nodes.items():
        if step in OUTCOME_STEPS or node["loss_rate"] is None:
            continue
        weighted = [
            (nodes[src]["loss_rate"], count)
            for src, count in incoming.get(step, [])
            if nodes[src]["loss_rate"] is not None
        ]
        total = sum(count for _, count in weighted)
        if not total:
            continue
        prev = sum(rate * count for rate, count in weighted) / total
        jump = node["loss_rate"] - prev
        if jump > 1e-9:
            points.append({
                "step": step,
                "loss_rate": _round(node["loss_rate"]),
                "prev_loss_rate": _round(prev),
                "jump": _round(jump),
            })
    points.sort(key=lambda p: (-p["jump"], p["step"]))
    return points[:TOP_N]


def escape_points(nodes: dict[str, dict], edges: Counter) -> list[dict]:
    """Steps most often followed directly by victim_exits."""
    points = []
    for (src, dst), count in edges.items():
        if dst != EXIT_STEP or src == EXIT_STEP:
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

    nodes = {
        step: {
            "step": step,
            "count": len(step_outcomes[step]),
            "loss_rate": loss_rate(step_outcomes[step]),
            "avg_position": sum(step_positions[step]) / len(step_positions[step]),
        }
        for step in vocab["steps"]
        if step in step_outcomes
    }

    counts = Counter(outcomes)
    return {
        "scam_type": scam_type,
        "n_stories": len(stories),
        "loss_rate": _round(loss_rate(outcomes)),
        "outcome_counts": {o: counts.get(o, 0) for o in vocab["outcomes"]},
        "nodes": [
            {**node, "loss_rate": _round(node["loss_rate"]), "avg_position": _round(node["avg_position"])}
            for node in sorted(nodes.values(), key=lambda n: (n["avg_position"], n["step"]))
        ],
        "edges": [
            {"from": src, "to": dst, "count": count}
            for (src, dst), count in sorted(edges.items(), key=lambda e: (-e[1], e[0]))
        ],
        "pinch_points": pinch_points(nodes, edges),
        "escape_points": escape_points(nodes, edges),
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
