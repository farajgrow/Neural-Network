"""Extract scam-related complaint narratives from archived CFPB bulk CSV zip.

Reads an archived CFPB complaints CSV zip (e.g. Wayback snapshot), filters for
long first-person scam narratives, buckets them into scam categories, selects up
to --per-bucket rows deterministically using a SHA-1 hash, and appends them to
data/raw/posts.jsonl using pipeline.scrape's PostStore / load_posts / write_posts.

Run from the repo root:
    python -m pipeline.cfpb_bulk [--zip PATH] [--per-bucket N] [--posts PATH]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import io
import re
import sys
import zipfile
from collections import Counter
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline import scrape

DEFAULT_ZIP = Path("data/raw/cfpb/wayback-2026-08-15/complaints.csv.zip")
DEFAULT_POSTS = Path("data/raw/posts.jsonl")
DEFAULT_PER_BUCKET = 200

MIN_NARRATIVE_CHARS = 400
SEED = "cfpb-bulk-v1"
WAYBACK_PREFIX = "cfpb:wayback-2026-08-15"

# Products excluded in filter step 2
EXCLUDED_PRODUCT_SUBSTRINGS = (
    "credit reporting",
    "debt collection",
    "mortgage",
    "student loan",
    "credit repair",
)

# Filter step 3 criteria
PRIMARY_PRODUCTS = frozenset({
    "Money transfer, virtual currency, or money service",
    "Money transfers",
    "Prepaid card",
})

SECONDARY_PRODUCTS = frozenset({
    "Checking or savings account",
    "Bank account or service",
    "Credit card or prepaid card",
})

SECONDARY_SCAM_RE = re.compile(
    r"\b(scammer|scammers|scammed|fraudster|fell victim)\b",
    re.IGNORECASE,
)

# Bucket categories in first-match order
BUCKETS = (
    "pig_butchering",
    "romance",
    "fake_job",
    "impersonation",
    "marketplace",
    "other",
)

PIG_BUTCHERING_CRYPTO_RE = re.compile(r"\b(crypto|bitcoin|usdt)\b", re.IGNORECASE)
PIG_BUTCHERING_ACTION_RE = re.compile(r"\b(platform|trading|withdraw)\b", re.IGNORECASE)
ROMANCE_RE = re.compile(r"\b(romance|dating|fell in love|boyfriend|girlfriend)\b", re.IGNORECASE)
FAKE_JOB_RE = re.compile(r"\b(job offer|recruiter|remote job)\b", re.IGNORECASE)
IMPERSONATION_RE = re.compile(
    r"\b(pretend(?:ed|ing) to be|posing as|impersonat\w*|spoof\w*|fraud department|IRS|SSA|remote access|AnyDesk)\b",
    re.IGNORECASE,
)
MARKETPLACE_RE = re.compile(r"\b(marketplace|seller|puppy|tickets?|rental)\b", re.IGNORECASE)


def set_csv_field_size_limit() -> None:
    """Set csv field size limit to the maximum allowed by C long on the current platform."""
    max_int = sys.maxsize
    while True:
        try:
            csv.field_size_limit(max_int)
            break
        except OverflowError:
            max_int = int(max_int / 10)


def is_excluded_product(product: str) -> bool:
    """Return True if the lowercased product contains any excluded substring."""
    product_lower = product.lower()
    return any(sub in product_lower for sub in EXCLUDED_PRODUCT_SUBSTRINGS)


def is_scam_complaint(product: str, issue: str, narrative: str) -> bool:
    """Return True if row meets primary or secondary scam criteria."""
    if issue == "Fraud or scam" and product in PRIMARY_PRODUCTS:
        return True
    if product in SECONDARY_PRODUCTS and bool(SECONDARY_SCAM_RE.search(narrative)):
        return True
    return False


def classify_bucket(narrative: str) -> str:
    """Bucket each kept row by keyword regexes; first match in precedence order wins."""
    if PIG_BUTCHERING_CRYPTO_RE.search(narrative) and PIG_BUTCHERING_ACTION_RE.search(narrative):
        return "pig_butchering"
    if ROMANCE_RE.search(narrative):
        return "romance"
    if FAKE_JOB_RE.search(narrative):
        return "fake_job"
    if IMPERSONATION_RE.search(narrative):
        return "impersonation"
    if MARKETPLACE_RE.search(narrative):
        return "marketplace"
    return "other"


def sha1_key(complaint_id: str) -> str:
    """Deterministic hash key for row ordering within a bucket."""
    return hashlib.sha1((SEED + complaint_id).encode("utf-8")).hexdigest()


class BoundedHeap:
    """Bounded min-order heap keeping the best N rows with smallest sha1 keys."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        # Stored tuples: (-key_int, counter, key_str, row)
        self.heap: list[tuple[int, int, str, dict[str, str]]] = []
        self._counter = 0

    def push(self, complaint_id: str, row: dict[str, str]) -> None:
        if self.capacity <= 0:
            return
        key_str = sha1_key(complaint_id)
        key_int = int(key_str, 16)
        self._counter += 1
        if len(self.heap) < self.capacity:
            heapq.heappush(self.heap, (-key_int, self._counter, key_str, row))
        elif -key_int > self.heap[0][0]:
            heapq.heapreplace(self.heap, (-key_int, self._counter, key_str, row))

    def get_sorted_rows(self) -> list[dict[str, str]]:
        """Return kept rows sorted ascending by sha1 key."""
        items = sorted(self.heap, key=lambda x: x[2])
        return [item[3] for item in items]

    def __len__(self) -> int:
        return len(self.heap)


def parse_date_received_utc(date_str: str) -> int:
    """Parse 'YYYY-MM-DD' into unix seconds at 00:00:00 UTC."""
    clean = date_str.strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(clean, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            pass
    return 0


def make_cfpb_post(
    complaint_id: str,
    narrative: str,
    date_received: str,
    bucket: str,
) -> dict[str, Any]:
    """Build a post dict matching pipeline.scrape.cfpb_post shape."""
    return {
        "id": f"cfpb-{complaint_id}",
        "source": "cfpb",
        "source_id": complaint_id,
        "title": "",
        "body": narrative,
        "created_utc": parse_date_received_utc(date_received),
        "score": None,
        "url": scrape.CFPB_COMPLAINT_URL.format(complaint_id),
        "fetched_via": f"{WAYBACK_PREFIX}:{bucket}",
        "subreddit": None,
    }


def stream_complaints_csv(zip_path: Path) -> Iterable[dict[str, str]]:
    """Stream CSV rows from the single member inside the complaints zip."""
    set_csv_field_size_limit()
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.infolist() if not m.is_dir() and m.filename.endswith(".csv")]
        if not members:
            members = [m for m in zf.infolist() if not m.is_dir()]
        if not members:
            return
        target = members[0]
        with zf.open(target) as bf:
            with io.TextIOWrapper(bf, encoding="utf-8", newline="") as tf:
                reader = csv.DictReader(tf)
                if reader.fieldnames:
                    reader.fieldnames = [fn.lstrip("\ufeff") for fn in reader.fieldnames]
                for row in reader:
                    yield row


@contextmanager
def _posts_path(path: Path) -> Generator[None, None, None]:
    # scrape's load_posts and write_posts helpers read the module-level OUT_PATH.
    orig = scrape.OUT_PATH
    try:
        scrape.OUT_PATH = path
        yield
    finally:
        scrape.OUT_PATH = orig


def process_cfpb_bulk(
    zip_path: Path,
    posts_path: Path,
    per_bucket: int = DEFAULT_PER_BUCKET,
) -> dict[str, Any]:
    """Filter, bucket, sample and append CFPB complaints to posts.jsonl."""
    store = scrape.PostStore()
    with _posts_path(posts_path):
        scrape.load_posts(store)

    seen_ids: set[str] = set(store.posts.keys())

    drop_counts: dict[str, int] = {
        "short_narrative": 0,
        "excluded_product": 0,
        "not_scam": 0,
        "duplicate_id": 0,
    }
    kept_before_cap: Counter[str] = Counter()
    heaps: dict[str, BoundedHeap] = {b: BoundedHeap(per_bucket) for b in BUCKETS}

    total_rows = 0
    for row in stream_complaints_csv(zip_path):
        total_rows += 1

        # Filter 1: narrative length >= 400
        narrative = row.get("Consumer complaint narrative") or ""
        if len(narrative.strip()) < MIN_NARRATIVE_CHARS:
            drop_counts["short_narrative"] += 1
            continue

        # Filter 2: excluded products
        product = (row.get("Product") or "").strip()
        if is_excluded_product(product):
            drop_counts["excluded_product"] += 1
            continue

        # Filter 3: primary or secondary scam criteria
        issue = (row.get("Issue") or "").strip()
        if not is_scam_complaint(product, issue, narrative):
            drop_counts["not_scam"] += 1
            continue

        # Filter 4: deduplication by complaint ID
        complaint_id = str(row.get("Complaint ID") or "").strip()
        post_id = f"cfpb-{complaint_id}"
        if not complaint_id or post_id in seen_ids:
            drop_counts["duplicate_id"] += 1
            continue
        seen_ids.add(post_id)

        # Kept row: bucket and push to bounded sampler
        bucket = classify_bucket(narrative)
        kept_before_cap[bucket] += 1
        heaps[bucket].push(complaint_id, row)

    # Append sampled posts to store in deterministic bucket order
    kept_after_cap: dict[str, int] = {}
    posts_appended = 0
    for bucket in BUCKETS:
        sorted_rows = heaps[bucket].get_sorted_rows()
        kept_after_cap[bucket] = len(sorted_rows)
        for row in sorted_rows:
            complaint_id = str(row.get("Complaint ID") or "").strip()
            narrative = row.get("Consumer complaint narrative") or ""
            date_received = row.get("Date received") or ""
            post = make_cfpb_post(complaint_id, narrative, date_received, bucket)
            if store.add(post):
                posts_appended += 1

    with _posts_path(posts_path):
        scrape.write_posts(store)

    return {
        "total_rows": total_rows,
        "drop_counts": drop_counts,
        "kept_before_cap": dict(kept_before_cap),
        "kept_after_cap": kept_after_cap,
        "posts_appended": posts_appended,
        "total_posts": len(store),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP, help="path to complaints CSV zip")
    parser.add_argument("--per-bucket", type=int, default=DEFAULT_PER_BUCKET, help="cap per bucket")
    parser.add_argument("--posts", type=Path, default=DEFAULT_POSTS, help="path to raw posts.jsonl")
    args = parser.parse_args(argv)

    results = process_cfpb_bulk(args.zip, args.posts, per_bucket=args.per_bucket)

    print(f"rows read: {results['total_rows']}")
    print("dropped per filter:")
    for filter_name, count in results["drop_counts"].items():
        print(f"  {filter_name:<18} {count}")
    print("kept per bucket (before cap -> after cap):")
    for bucket in BUCKETS:
        before = results["kept_before_cap"].get(bucket, 0)
        after = results["kept_after_cap"].get(bucket, 0)
        print(f"  {bucket:<18} {before:>5} -> {after:>5}")
    print(f"posts appended: {results['posts_appended']}")
    print(f"total posts in {args.posts}: {results['total_posts']}")


if __name__ == "__main__":
    main()
