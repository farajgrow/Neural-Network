"""Hand-check extracted stories against their source posts (task P4).

Picks a seeded random sample of stories from ``data/stories.json`` that have a
source post in ``data/clean/posts.jsonl`` (mock stories are skipped), shows
each post next to its story, and asks whether each field is right:

- ``scam_type``: the right scam type
- ``events``: the steps are right and in a sensible order (small slips in a
  detail are fine; a missing or invented key step is not)
- ``outcome``: lost_money / escaped / unknown is right
- ``amount_lost_usd``: the amount, or null, matches the post

Answer ``y`` (right), ``n`` (wrong) or ``?`` (can't tell, left out of the
accuracy). ``q`` quits; every finished story is saved straight away to
``data/spotcheck/results.jsonl``, so a rerun with the same seed picks up where
you left off.

``--report`` prints the accuracy per field and overall, ready for the README.

Usage::

    python -m pipeline.spotcheck [--n 20] [--seed 0]
    python -m pipeline.spotcheck --report
"""

from __future__ import annotations

import argparse
import json
import random
import textwrap
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

DEFAULT_STORIES = Path("data/stories.json")
DEFAULT_POSTS = Path("data/clean/posts.jsonl")
DEFAULT_RESULTS = Path("data/spotcheck/results.jsonl")

DEFAULT_N = 20
DEFAULT_SEED = 0
WRAP_WIDTH = 100

# Order matters: this is the order fields are asked about and reported in.
FIELDS = ("scam_type", "events", "outcome", "amount_lost_usd")
ANSWERS = {"y": True, "n": False, "?": None}


class Quit(Exception):
    """The reviewer typed ``q``."""


# --------------------------------------------------------------------------
# Sampling and results file
# --------------------------------------------------------------------------


def pick_sample(
    stories: list[dict[str, Any]], post_ids: Iterable[str], n: int, seed: int
) -> list[dict[str, Any]]:
    """``n`` stories that have a source post, chosen reproducibly from ``seed``."""
    known = set(post_ids)
    eligible = sorted((s for s in stories if s["id"] in known), key=lambda s: s["id"])
    return random.Random(seed).sample(eligible, min(n, len(eligible)))


def load_results(path: Path) -> dict[str, dict[str, Any]]:
    """Saved judgements by story id. A later line for the same id wins."""
    results: dict[str, dict[str, Any]] = {}
    if path.exists():
        with path.open() as f:
            for line in f:
                if line.strip():
                    result = json.loads(line)
                    results[result["id"]] = result
    return results


def append_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# Reviewing
# --------------------------------------------------------------------------


def wrap(text: str, indent: str = "  ") -> str:
    paragraphs = text.split("\n\n")
    return "\n\n".join(
        textwrap.fill(p, WRAP_WIDTH, initial_indent=indent, subsequent_indent=indent)
        for p in paragraphs
    )


def format_story(story: dict[str, Any]) -> str:
    lines = [
        f"  scam_type        {story['scam_type']}",
        "  events",
        *(
            f"    {i:2}. {e['step']:<22} {e['actor']:<11} {e['detail']}"
            for i, e in enumerate(story["events"], 1)
        ),
        f"  outcome          {story['outcome']}",
        f"  amount_lost_usd  {story['amount_lost_usd']}",
        "  snippet",
        wrap(story["snippet"], indent="    "),
    ]
    return "\n".join(lines)


def ask_answer(field: str, ask: Callable[[str], str]) -> bool | None:
    while True:
        answer = ask(f"  {field} right? [y/n/?/q] ").strip().lower()
        if answer == "q":
            raise Quit
        if answer in ANSWERS:
            return ANSWERS[answer]


def review_story(
    story: dict[str, Any],
    post: dict[str, Any],
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Show one post and its story, and return the reviewer's judgements."""
    show(f"\n{'=' * WRAP_WIDTH}\nPOST {post['id']}\n")
    show(wrap(post["text"]))
    show(f"\nSTORY\n{format_story(story)}\n")
    checks = {field: ask_answer(field, ask) for field in FIELDS}
    note = ask("  note (optional): ").strip()
    return {"id": story["id"], "checks": checks, "note": note}


def review(
    sample: list[dict[str, Any]],
    posts: dict[str, dict[str, Any]],
    results_path: Path,
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> int:
    """Review every story in ``sample`` not already in the results file. Returns how many were done."""
    done = load_results(results_path)
    todo = [s for s in sample if s["id"] not in done]
    show(f"{len(sample) - len(todo)} of {len(sample)} already checked, {len(todo)} to go")
    n_done = 0
    for i, story in enumerate(todo, 1):
        show(f"\n[{i}/{len(todo)}]")
        try:
            result = review_story(story, posts[story["id"]], ask, show)
        except (Quit, EOFError):
            show("\nstopped; progress is saved")
            break
        append_result(results_path, result)
        n_done += 1
    return n_done


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def accuracy(results: Iterable[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    """``(right, judged)`` per field, plus ``"all_fields"`` and ``"overall"``.

    ``all_fields`` counts stories where every judged field was right.
    ``overall`` pools every judged field across every story. ``?`` answers
    are left out of both counts.
    """
    counts = {field: [0, 0] for field in (*FIELDS, "all_fields", "overall")}
    for result in results:
        judged = [(f, ok) for f, ok in result["checks"].items() if ok is not None]
        for field, ok in judged:
            counts[field][0] += ok
            counts[field][1] += 1
            counts["overall"][0] += ok
            counts["overall"][1] += 1
        if judged:
            counts["all_fields"][0] += all(ok for _, ok in judged)
            counts["all_fields"][1] += 1
    return {field: (right, total) for field, (right, total) in counts.items()}


def format_report(counts: dict[str, tuple[int, int]]) -> str:
    def pct(right: int, total: int) -> str:
        return f"{100 * right / total:5.1f}%" if total else "    -"

    lines = [f"  {field:<16} {pct(*counts[field])}  ({right}/{total})"
             for field, (right, total) in counts.items()]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def read_posts(path: Path) -> dict[str, dict[str, Any]]:
    with path.open() as f:
        return {post["id"]: post for post in map(json.loads, filter(str.strip, f))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stories", type=Path, default=DEFAULT_STORIES)
    parser.add_argument("--posts", type=Path, default=DEFAULT_POSTS)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="stories to check")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--report", action="store_true", help="print accuracy and exit")
    args = parser.parse_args(argv)

    if args.report:
        results = list(load_results(args.results).values())
        if not results:
            print(f"no results in {args.results} yet")
            return 1
        print(f"spot-check of {len(results)} stories:")
        print(format_report(accuracy(results)))
        return 0

    stories = json.loads(args.stories.read_text())
    posts = read_posts(args.posts)
    sample = pick_sample(stories, posts, args.n, args.seed)
    if not sample:
        print(f"no stories in {args.stories} have a source post in {args.posts}")
        return 1
    review(sample, posts, args.results)
    print(f"\nresults in {args.results}; run with --report for accuracy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
