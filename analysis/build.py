"""Build analysis/graphs.json from the stories file.

Usage: python -m analysis.build [--stories PATH] [--out PATH] [--no-clusters]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from analysis.clusters import build_clusters, summarize as summarize_clusters
from analysis.graphs import REPO_ROOT, build_graphs_file, load_stories, load_vocab

DEFAULT_STORIES = REPO_ROOT / "data" / "stories.json"
DEFAULT_OUT = REPO_ROOT / "analysis" / "graphs.json"


def summarize(graphs_file: dict) -> str:
    """One block of text per graph listing its pinch and escape points."""
    lines = [f"{graphs_file['n_stories']} stories, types: {', '.join(graphs_file['scam_types'])}"]
    for name, graph in graphs_file["graphs"].items():
        lines.append(f"\n[{name}] n={graph['n_stories']} loss_rate={graph['loss_rate']}")
        for p in graph["pinch_points"]:
            lines.append(f"  pinch  {p['step']:<24} {p['prev_loss_rate']} -> {p['loss_rate']} (+{p['jump']})")
        for p in graph["escape_points"]:
            lines.append(f"  escape {p['step']:<24} exits={p['exit_count']} rate={p['exit_rate']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stories", type=Path, default=DEFAULT_STORIES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-clusters", action="store_true", help="skip sequence clustering")
    args = parser.parse_args(argv)

    stories, vocab = load_stories(args.stories), load_vocab()
    graphs_file = build_graphs_file(stories, vocab)
    if not args.no_clusters:
        graphs_file["clusters"] = build_clusters(stories, vocab)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(graphs_file, f, indent=2)
        f.write("\n")

    print(summarize(graphs_file))
    print("\n" + summarize_clusters(graphs_file["clusters"]))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
