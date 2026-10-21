"""Pick which new clean posts to extract next.

Reads data/clean/posts.jsonl (plus data/raw/posts.jsonl for each post's
subreddit) and writes data/clean/selected.jsonl. A post is skipped when:

  1. it already has an extraction cache entry, or is listed in data/exclude.json;
  2. the relevance rules say it is not a first-person story of events
     (see `relevance_reasons`);
  3. it is a near-duplicate of a post kept earlier (crossposts and reposts);
  4. its subreddit has reached its quota.

Posts are taken in a seeded order, so reruns pick the same posts.

Run from the repo root:
    .venv/bin/python -m pipeline.select_posts [--total 1000]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zlib
from collections import Counter, defaultdict
from pathlib import Path

from pipeline.clean import read_jsonl, write_jsonl
from pipeline.extract import cache_path, load_exclude
from pipeline.scrape import SUBREDDIT_SOURCES

CLEAN_PATH = Path("data/clean/posts.jsonl")
RAW_PATH = Path("data/raw/posts.jsonl")
OUT_PATH = Path("data/clean/selected.jsonl")

SEED = "select-v1"
SHINGLE_WORDS = 5
MAX_JACCARD = 0.5
MAX_CONTAINMENT = 0.8
MIN_PAST_VERBS = 5

# CFPB complaint narratives from the archived bulk file (fetched_via "cfpb:wayback..."),
# selected as one bucket next to the subreddits.
CFPB_BUCKET = "cfpb"
CFPB_QUOTA = 300

# Subreddits that are mostly off-topic: a post must also name a scam.
OFF_TOPIC = {"FacebookMarketplace", "recruitinghell", "jobs", "legaladvice", "personalfinance"}

FIRST_PERSON_RE = re.compile(r"\b(?:i|i'm|i've|i'd|i'll|me|my|mine|myself|we|our|us)\b", re.I)
PAST_RE = re.compile(
    r"\b(?:sent|paid|lost|gave|bought|sold|got|said|told|met|took|made|came|went|left|found|"
    r"thought|knew|saw|felt|kept|spent|began|wrote|spoke|rang|stole|won|put|let|hit|cut|ran|"
    r"did|had|was|were|[a-z]{3,}ed)\b",
    re.I,
)
SCAM_WORD_RE = re.compile(
    r"\b(?:scam\w*|fraud\w*|con ?artist|phish\w*|fake|stolen?|impersonat\w*|swindl\w*|"
    r"extort\w*|catfish\w*|pig.?butcher\w*)\b",
    re.I,
)
PSA_TITLE_RE = re.compile(r"^\s*[\[(]?(?:psa|warning|beware|heads up|reminder|tip|guide|faq|list of)\b", re.I)


def relevance_reasons(text: str, off_topic: bool = False) -> list[str]:
    """Why a clean post is not a first-person scam story; empty when it is one."""
    title, _, body = text.partition("\n\n")
    if not body:
        title, body = "", text
    n_words = max(1, len(re.findall(r"[\w']+", body)))
    first_person = len(FIRST_PERSON_RE.findall(body))
    past = len(PAST_RE.findall(body))
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
    questions = sum(s.rstrip().endswith("?") for s in sentences) / max(1, len(sentences))

    reasons = []
    if first_person == 0:
        reasons.append("not_first_person")
    if past < MIN_PAST_VERBS:
        reasons.append("no_narrative")
    if questions >= 0.5 and past < 8:
        reasons.append("question_only")
    if PSA_TITLE_RE.search(title) and first_person / n_words < 0.02:
        reasons.append("psa_or_guide")
    if off_topic and not SCAM_WORD_RE.search(text):
        reasons.append("no_scam_word")
    return reasons


def shingles(text: str) -> set[int]:
    words = re.findall(r"\w+", text.lower())
    return {
        zlib.crc32(" ".join(words[i : i + SHINGLE_WORDS]).encode())
        for i in range(max(1, len(words) - SHINGLE_WORDS + 1))
    }


class NearDuplicates:
    """Remembers kept posts and finds a new post's near-duplicate among them."""

    def __init__(self) -> None:
        self.sets: dict[str, set[int]] = {}
        self.index: dict[int, list[str]] = defaultdict(list)

    def match(self, sh: set[int]) -> str | None:
        overlap: Counter = Counter(i for s in sh for i in self.index.get(s, ()))
        for other, common in overlap.most_common(5):
            other_sh = self.sets[other]
            if (common / len(sh | other_sh) >= MAX_JACCARD
                    or common / min(len(sh), len(other_sh)) >= MAX_CONTAINMENT):
                return other
        return None

    def add(self, post_id: str, sh: set[int]) -> None:
        self.sets[post_id] = sh
        for s in sh:
            self.index[s].append(post_id)


def order_key(post_id: str) -> str:
    return hashlib.sha1(f"{SEED}:{post_id}".encode()).hexdigest()


def select(
    clean: list[dict], subreddit_of: dict[str, str], quotas: dict[str, int],
    exclude: set[str], total: int,
) -> tuple[list[dict], Counter]:
    """Return (selected posts with a `subreddit` field, counts of skip reasons)."""
    stats: Counter = Counter()
    seen = NearDuplicates()
    candidates = []
    for post in clean:
        sh = shingles(post["text"])
        if post["id"] in exclude:
            stats["excluded"] += 1
        elif cache_path(post["id"]).exists():
            seen.add(post["id"], sh)  # already extracted: new posts must not repeat it
        else:
            candidates.append((post, sh))

    taken: Counter = Counter()
    selected = []
    for post, sh in sorted(candidates, key=lambda c: order_key(c[0]["id"])):
        sub = subreddit_of.get(post["id"], "")
        if sub not in quotas:
            stats["no_quota"] += 1
            continue
        reasons = relevance_reasons(post["text"], off_topic=sub in OFF_TOPIC)
        if reasons:
            stats[f"not_a_story:{reasons[0]}"] += 1
            continue
        if seen.match(sh):
            stats["near_duplicate"] += 1
            continue
        if taken[sub] >= quotas[sub] or len(selected) >= total:
            stats["over_quota"] += 1
            continue
        seen.add(post["id"], sh)
        taken[sub] += 1
        selected.append({**post, "subreddit": sub})
    return selected, stats


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--total", type=int, default=1000, help="most posts to select")
    parser.add_argument("--output", type=Path, default=OUT_PATH)
    args = parser.parse_args(argv)

    # Only posts found by SUBREDDIT_SOURCES or the archived CFPB file count toward
    # a quota. The older r/Scams listings are already extracted, and still guard
    # against repeats.
    subreddit_of = {}
    for p in read_jsonl(RAW_PATH):
        if p["fetched_via"].startswith("arctic:r/"):
            subreddit_of[p["id"]] = p.get("subreddit") or ""
        elif p["fetched_via"].startswith("cfpb:wayback"):
            subreddit_of[p["id"]] = CFPB_BUCKET
    quotas = {sub: quota for sub, (quota, _) in SUBREDDIT_SOURCES.items()}
    quotas[CFPB_BUCKET] = CFPB_QUOTA

    selected, stats = select(read_jsonl(CLEAN_PATH), subreddit_of, quotas, load_exclude(), args.total)
    write_jsonl(args.output, selected)

    print(f"selected {len(selected)} posts -> {args.output}")
    for sub, n in Counter(p["subreddit"] for p in selected).most_common():
        print(f"  r/{sub:<20} {n}")
    print("skipped:")
    for reason, n in stats.most_common():
        print(f"  {reason:<28} {n}")


if __name__ == "__main__":
    main()
