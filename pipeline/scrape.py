"""Scrape raw scam victim stories into data/raw/posts.jsonl.

Sources, in order:
  1. Reddit's public JSON listing for r/Scams (often blocked; tried once).
  2. Arctic Shift, a Reddit archive API: keyword searches per scam type,
     a high-score slice sampled across many years, then newest posts to
     top up the total.
  3. CFPB consumer complaint narratives, when the API serves them.

Output format is documented in pipeline/README.md.

Run from the repo root:
    .venv/bin/python -m pipeline.scrape
"""

import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

OUT_PATH = Path("data/raw/posts.jsonl")

USER_AGENT = (
    "scam-sequences/0.1 "
    "(+https://github.com/farajgrow/Neural-Network-graph-for-scam-detection)"
)
REQUEST_INTERVAL = 1.0  # seconds between requests, across all sources
MAX_ATTEMPTS = 5
TIMEOUT = 90

MIN_BODY_CHARS = 400
TARGET_TOTAL = 700

SUBREDDIT = "Scams"
REDDIT_TOP_URL = f"https://www.reddit.com/r/{SUBREDDIT}/top.json?t=all&limit=100"
ARCTIC_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
ARCTIC_FIELDS = "id,title,selftext,score,created_utc"
CFPB_URL = "https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/"
CFPB_COMPLAINT_URL = "https://www.consumerfinance.gov/data-research/consumer-complaints/search/detail/{}"

# Keyword searches, grouped by the scam type they are meant to cover.
# Each search pages backwards in time until it has PER_QUERY_TARGET usable posts.
ARCTIC_QUERIES = {
    "pig_butchering": ["pig butchering", "crypto investment", "wrong number"],
    "fake_job": ["task job", "remote job recruiter", "telegram job"],
    "impersonation": ["bank fraud department", "IRS", "police warrant", "tech support"],
    "marketplace": ["marketplace", "zelle", "overpayment"],
    "romance": ["romance", "dating"],
}
PER_QUERY_TARGET = 35
MAX_PAGES_PER_QUERY = 4

# High-score slice: sample one window every TOP_STEP_MONTHS months and keep the
# highest-scoring usable posts from it. Arctic Shift cannot sort by score, so
# this spreads coverage over years instead of the most recent weeks.
TOP_FIRST_MONTH = (2018, 1)
TOP_STEP_MONTHS = 2
TOP_PER_WINDOW = 3

CFPB_SEARCH_TERMS = ["scam", "fraud"]
CFPB_TARGET = 150

REMOVED_MARKERS = {"[removed]", "[deleted]", ""}


def is_overload(err: urllib.error.HTTPError) -> bool:
    """Arctic Shift answers slow searches with 422 and a "Timeout" message.

    The query keeps running server side, so a retry a few seconds later
    usually succeeds from its cache.
    """
    if err.code != 422:
        return False
    try:
        return "timeout" in err.read().decode("utf-8", "replace").lower()
    except OSError:
        return False


class Fetcher:
    """Polite JSON fetcher: one shared rate limit, retries with backoff."""

    def __init__(self) -> None:
        self._last_request = 0.0
        self.requests = 0

    def get_json(self, url: str, retries: bool = True) -> dict | None:
        attempts = MAX_ATTEMPTS if retries else 1
        for attempt in range(1, attempts + 1):
            self._wait_turn()
            try:
                data = self._request(url)
            except urllib.error.HTTPError as err:
                if err.code == 429 or err.code >= 500 or is_overload(err):
                    self._backoff(attempt, attempts, f"HTTP {err.code}")
                    continue
                print(f"    HTTP {err.code}, giving up on {url[:90]}")
                return None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
                self._backoff(attempt, attempts, type(err).__name__)
                continue

            error = data.get("error") if isinstance(data, dict) else None
            if error:
                print(f"    API error: {error}")
                return None
            return data
        return None

    def _request(self, url: str) -> dict:
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        self.requests += 1
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)

    def _wait_turn(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)
        self._last_request = time.monotonic()

    @staticmethod
    def _backoff(attempt: int, attempts: int, reason: str) -> None:
        if attempt == attempts:
            print(f"    {reason}, out of retries")
            return
        delay = 5 * 2 ** (attempt - 1)
        print(f"    {reason}, retrying in {delay}s")
        time.sleep(delay)


class PostStore:
    """Collects posts in insertion order, deduplicated by id."""

    def __init__(self) -> None:
        self.posts: dict[str, dict] = {}

    def add(self, post: dict) -> bool:
        if post["id"] in self.posts:
            return False
        self.posts[post["id"]] = post
        return True

    def __len__(self) -> int:
        return len(self.posts)


def usable_body(body: str | None) -> bool:
    body = (body or "").strip()
    return body not in REMOVED_MARKERS and len(body) >= MIN_BODY_CHARS


def reddit_post(raw: dict, fetched_via: str) -> dict:
    post_id = raw["id"]
    return {
        "id": f"reddit-{post_id}",
        "source": "reddit",
        "source_id": post_id,
        "title": raw.get("title") or "",
        "body": raw.get("selftext") or "",
        "created_utc": int(raw["created_utc"]),
        "score": raw.get("score"),
        "url": f"https://www.reddit.com/r/{SUBREDDIT}/comments/{post_id}/",
        "fetched_via": fetched_via,
    }


# --- Reddit public JSON -----------------------------------------------------


def scrape_reddit_top(fetcher: Fetcher, store: PostStore) -> None:
    print("reddit: trying the public JSON listing once")
    data = fetcher.get_json(REDDIT_TOP_URL, retries=False)
    if not data:
        print("reddit: unavailable, continuing with Arctic Shift")
        return
    children = data.get("data", {}).get("children", [])
    added = sum(
        store.add(reddit_post(child["data"], "reddit:top"))
        for child in children
        if usable_body(child["data"].get("selftext"))
    )
    print(f"reddit: +{added} posts")


# --- Arctic Shift -----------------------------------------------------------


def arctic_url(**params: str | int) -> str:
    query = {"subreddit": SUBREDDIT, "limit": 100, "fields": ARCTIC_FIELDS, **params}
    return f"{ARCTIC_URL}?{urllib.parse.urlencode(query)}"


def arctic_pages(fetcher: Fetcher, max_pages: int, **params: str | int):
    """Yield pages of posts, newest first, paging backwards with `before`."""
    before = params.pop("before", None)
    for _ in range(max_pages):
        page_params = dict(params)
        if before is not None:
            page_params["before"] = before
        data = fetcher.get_json(arctic_url(**page_params))
        posts = (data or {}).get("data") or []
        if not posts:
            return
        yield posts
        before = min(int(p["created_utc"]) for p in posts)


def scrape_arctic_queries(fetcher: Fetcher, store: PostStore) -> None:
    for scam_type, queries in ARCTIC_QUERIES.items():
        for query in queries:
            label = f"arctic:{query}"
            added = 0
            for page in arctic_pages(fetcher, MAX_PAGES_PER_QUERY, query=query):
                for raw in page:
                    if usable_body(raw.get("selftext")):
                        added += store.add(reddit_post(raw, label))
                    if added >= PER_QUERY_TARGET:
                        break
                if added >= PER_QUERY_TARGET:
                    break
            print(f"{label:<32} ({scam_type}) +{added:>3}   total {len(store)}")


def month_windows(first: tuple[int, int], step: int):
    """Yield (after, before) unix-second pairs for one-month windows up to now."""
    now = datetime.now(timezone.utc)
    year, month = first
    while (year, month) < (now.year, now.month):
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
        end = datetime(end_year, end_month, 1, tzinfo=timezone.utc)
        yield int(start.timestamp()), int(end.timestamp())
        month += step
        year, month = year + (month - 1) // 12, (month - 1) % 12 + 1


def scrape_arctic_top(fetcher: Fetcher, store: PostStore) -> None:
    added = 0
    for after, before in month_windows(TOP_FIRST_MONTH, TOP_STEP_MONTHS):
        data = fetcher.get_json(arctic_url(after=after, before=before))
        posts = [p for p in (data or {}).get("data") or [] if usable_body(p.get("selftext"))]
        posts.sort(key=lambda p: p.get("score") or 0, reverse=True)
        window_added = sum(
            store.add(reddit_post(raw, "arctic:top")) for raw in posts[:TOP_PER_WINDOW]
        )
        added += window_added
        month = datetime.fromtimestamp(after, timezone.utc).strftime("%Y-%m")
        print(f"arctic:top  {month}  +{window_added}   total {len(store)}")
    print(f"arctic:top done, +{added}")


def scrape_arctic_new(fetcher: Fetcher, store: PostStore, target_total: int) -> None:
    if len(store) >= target_total:
        return
    added = 0
    for page in arctic_pages(fetcher, max_pages=20):
        for raw in page:
            if usable_body(raw.get("selftext")):
                added += store.add(reddit_post(raw, "arctic:new"))
            if len(store) >= target_total:
                break
        print(f"arctic:new  +{added}   total {len(store)}")
        if len(store) >= target_total:
            return


# --- CFPB -------------------------------------------------------------------


def cfpb_narrative(source: dict) -> str:
    # The field has been published under both names over time.
    return source.get("complaint_what_happened") or source.get("complaint_narrative") or ""


def cfpb_post(source: dict, fetched_via: str) -> dict:
    complaint_id = str(source["complaint_id"])
    received = datetime.fromisoformat(source["date_received"].replace("Z", "+00:00"))
    return {
        "id": f"cfpb-{complaint_id}",
        "source": "cfpb",
        "source_id": complaint_id,
        "title": "",
        "body": cfpb_narrative(source),
        "created_utc": int(received.timestamp()),
        "score": None,
        "url": CFPB_COMPLAINT_URL.format(complaint_id),
        "fetched_via": fetched_via,
    }


def scrape_cfpb(fetcher: Fetcher, store: PostStore) -> None:
    added = 0
    for term in CFPB_SEARCH_TERMS:
        params = {"search_term": term, "has_narrative": "true", "size": 100, "no_aggs": "true"}
        data = fetcher.get_json(f"{CFPB_URL}?{urllib.parse.urlencode(params)}")
        hits = (data or {}).get("hits", {}).get("hits", [])
        with_text = [h["_source"] for h in hits if usable_body(cfpb_narrative(h["_source"]))]
        for source in with_text:
            if added >= CFPB_TARGET:
                break
            added += store.add(cfpb_post(source, f"cfpb:{term}"))
        print(f"cfpb:{term:<10} {len(hits)} hits, {len(with_text)} with narratives   +{added} so far")
        if hits and not with_text:
            print("cfpb: the API returned complaints without narrative text; skipping CFPB")
            return
        if added >= CFPB_TARGET:
            return


# --- Output -----------------------------------------------------------------


def write_posts(store: PostStore) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as out:
        for post in store.posts.values():
            out.write(json.dumps(post, ensure_ascii=False) + "\n")


def print_summary(store: PostStore, fetcher: Fetcher, seconds: float) -> None:
    posts = list(store.posts.values())
    print(f"\nwrote {len(posts)} posts to {OUT_PATH} ({fetcher.requests} requests, {seconds:.0f}s)")

    print("\nper source:")
    for source, count in Counter(p["source"] for p in posts).most_common():
        print(f"  {source:<10} {count}")

    print("\nper fetched_via:")
    for via, count in Counter(p["fetched_via"] for p in posts).most_common():
        print(f"  {via:<32} {count}")

    lengths = sorted(len(p["body"]) for p in posts)
    if len(lengths) >= 2:
        pct = statistics.quantiles(lengths, n=100)
        print("\nbody length (chars):")
        print(f"  min {lengths[0]}  p10 {pct[9]:.0f}  p25 {pct[24]:.0f}  median {pct[49]:.0f}"
              f"  p75 {pct[74]:.0f}  p90 {pct[89]:.0f}  max {lengths[-1]}")


def main() -> None:
    started = time.monotonic()
    fetcher = Fetcher()
    store = PostStore()

    scrape_reddit_top(fetcher, store)
    scrape_arctic_queries(fetcher, store)
    scrape_arctic_top(fetcher, store)
    scrape_cfpb(fetcher, store)
    scrape_arctic_new(fetcher, store, TARGET_TOTAL)

    write_posts(store)
    print_summary(store, fetcher, time.monotonic() - started)


if __name__ == "__main__":
    main()
