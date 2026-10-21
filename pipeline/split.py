"""Near-duplicate-aware train/test split of scam stories.

Groups stories by near-duplicate text using consecutive 5-word shingles and
containment similarity (>= 0.3) with transitive grouping via union-find.
Each group is assigned deterministically to test (20%) or train (80%) based
on a SHA-256 hash of the lexicographically smallest ID in the group.

Story text is retrieved from clean posts (``data/clean/posts.jsonl``) matching
the story's ``id``, falling back to the story's ``snippet`` if no clean post exists.

Usage::

    python -m pipeline.split [--stories data/stories.json] [--posts data/clean/posts.jsonl] [--output data/split.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pipeline.clean import read_jsonl

DEFAULT_STORIES = Path("data/stories.json")
DEFAULT_POSTS = Path("data/clean/posts.jsonl")
DEFAULT_OUTPUT = Path("data/split.json")

SHINGLE_SIZE = 5
SIMILARITY_THRESHOLD = 0.3
TEST_RATIO_MOD = 10
TEST_THRESHOLD = 2  # hash % 10 in {0, 1} -> 20% test


class UnionFind:
    """Disjoint-set data structure with path compression and union by rank."""

    def __init__(self, elements: Iterable[str]) -> None:
        self.parent: dict[str, str] = {x: x for x in elements}
        self.rank: dict[str, int] = {x: 0 for x in elements}

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        curr = x
        while curr != root:
            nxt = self.parent[curr]
            self.parent[curr] = root
            curr = nxt
        return root

    def union(self, x: str, y: str) -> None:
        rx = self.find(x)
        ry = self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            self.parent[rx] = ry
        elif self.rank[rx] > self.rank[ry]:
            self.parent[ry] = rx
        else:
            self.parent[ry] = rx
            self.rank[rx] += 1


def shingles(text: str, k: int = SHINGLE_SIZE) -> set[tuple[str, ...]]:
    """Extract consecutive k-word shingles from lowercased text.

    If the text has fewer than k words, a single shingle with all words is returned.
    If the text has no words, an empty set is returned.
    """
    words = re.findall(r"[a-z0-9']+", text.lower())
    if not words:
        return set()
    if len(words) < k:
        return {tuple(words)}
    return {tuple(words[i : i + k]) for i in range(len(words) - k + 1)}


def containment(a: set, b: set) -> float:
    """Calculate containment similarity: |A ∩ B| / min(|A|, |B|).

    Returns 0.0 if either set is empty.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def group_ids(texts: dict[str, str], threshold: float = SIMILARITY_THRESHOLD) -> list[list[str]]:
    """Group post IDs by near-duplicate text similarity using union-find.

    Any pair with containment >= threshold is placed in the same group transitively.
    Uses an inverted index from shingles to post IDs so only pairs sharing at least
    one shingle are compared.

    Returns all groups (including singletons), with each group sorted and the list
    of groups sorted by their minimum ID.
    """
    if not texts:
        return []

    post_shingles = {pid: shingles(text) for pid, text in texts.items()}

    inverted_index: dict[tuple[str, ...], list[str]] = {}
    for pid, sh_set in post_shingles.items():
        for sh in sh_set:
            inverted_index.setdefault(sh, []).append(pid)

    uf = UnionFind(texts.keys())

    sorted_pids = sorted(texts.keys())
    for pid in sorted_pids:
        sh_set = post_shingles[pid]
        if not sh_set:
            continue
        checked: set[str] = set()
        for sh in sh_set:
            for other in inverted_index[sh]:
                if other > pid and other not in checked:
                    checked.add(other)
                    if containment(sh_set, post_shingles[other]) >= threshold:
                        uf.union(pid, other)

    groups_by_root: dict[str, list[str]] = {}
    for pid in sorted_pids:
        root = uf.find(pid)
        groups_by_root.setdefault(root, []).append(pid)

    result = [sorted(g) for g in groups_by_root.values()]
    result.sort(key=lambda g: g[0])
    return result


def assign(groups: Iterable[Iterable[str]]) -> dict[str, list[str]]:
    """Assign groups wholly to test or train based on SHA-256 of min group ID.

    Test when int(sha256(min_id).hexdigest(), 16) % 10 < 2 (~20%), else train (~80%).
    Returns {"train": [sorted ids], "test": [sorted ids]}.
    """
    train: list[str] = []
    test: list[str] = []

    for group in groups:
        group_list = list(group)
        if not group_list:
            continue
        min_id = min(group_list)
        digest = hashlib.sha256(min_id.encode("utf-8")).hexdigest()
        if int(digest, 16) % TEST_RATIO_MOD < TEST_THRESHOLD:
            test.extend(group_list)
        else:
            train.extend(group_list)

    return {"train": sorted(train), "test": sorted(test)}


def split(
    stories: Iterable[dict[str, Any]],
    posts: Iterable[dict[str, Any]] = (),
    threshold: float = SIMILARITY_THRESHOLD,
) -> dict[str, Any]:
    """Perform near-duplicate aware train/test split on stories.

    Only story IDs are split. Post text is used if available for the story ID,
    falling back to the story's snippet.
    """
    post_texts = {p["id"]: p.get("text", "") for p in posts}
    texts = {
        s["id"]: post_texts[s["id"]] if s["id"] in post_texts else (s.get("snippet") or "")
        for s in stories
    }

    groups = group_ids(texts, threshold=threshold)
    assignment = assign(groups)
    multi_groups = [g for g in groups if len(g) >= 2]

    return {
        "train": assignment["train"],
        "test": assignment["test"],
        "groups": multi_groups,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stories", type=Path, default=DEFAULT_STORIES, help="stories JSON file")
    parser.add_argument("--posts", type=Path, default=DEFAULT_POSTS, help="clean posts JSONL file")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="output split JSON file")
    args = parser.parse_args(argv)

    with args.stories.open(encoding="utf-8") as f:
        stories = json.load(f)

    posts = read_jsonl(args.posts) if args.posts.exists() else []
    result = split(stories, posts)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        f.write(json.dumps(result, indent=2) + "\n")

    n_stories = len(result["train"]) + len(result["test"])
    n_groups = len(result["groups"])
    n_train = len(result["train"])
    n_test = len(result["test"])
    print(f"stories: {n_stories}, groups with 2+ members: {n_groups}, train: {n_train}, test: {n_test}")


if __name__ == "__main__":
    main()
