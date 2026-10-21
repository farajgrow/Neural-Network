"""Tests for pipeline.scrape. No network: urlopen and the fetcher are faked."""

import io
import json
import urllib.error
import urllib.parse
from datetime import datetime, timezone

import pytest

from pipeline import scrape

LONG = "I got a text from a wrong number and it turned into a crypto scam. " * 10


def raw_reddit(post_id, selftext=LONG, created=1_700_000_000, score=10, title="Title"):
    return {"id": post_id, "title": title, "selftext": selftext,
            "created_utc": created, "score": score}


def http_error(code, body=b""):
    return urllib.error.HTTPError("https://x", code, "msg", {}, io.BytesIO(body))


class FakeFetcher:
    """Answers get_json from a function of the URL and records every URL asked for."""

    def __init__(self, answer):
        self.answer = answer
        self.urls = []
        self.requests = 0

    def get_json(self, url, retries=True):
        self.urls.append(url)
        self.requests += 1
        return self.answer(url)


def query_of(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


@pytest.fixture
def sleeps(monkeypatch):
    """Patch time.sleep so retries run instantly; returns the delays asked for."""
    delays = []
    monkeypatch.setattr(scrape.time, "sleep", delays.append)
    return delays


# --- pure helpers -----------------------------------------------------------


@pytest.mark.parametrize("body, ok", [
    (None, False),
    ("", False),
    ("[removed]", False),
    ("[deleted]", False),
    ("x" * (scrape.MIN_BODY_CHARS - 1), False),
    ("   " + "x" * (scrape.MIN_BODY_CHARS - 1) + "   ", False),  # whitespace doesn't count
    ("x" * scrape.MIN_BODY_CHARS, True),
    (LONG, True),
])
def test_usable_body(body, ok):
    assert scrape.usable_body(body) is ok


def test_reddit_post_fields():
    post = scrape.reddit_post(raw_reddit("abc123", created=1_700_000_000.0), "arctic:top")
    assert post == {
        "id": "reddit-abc123",
        "source": "reddit",
        "source_id": "abc123",
        "title": "Title",
        "body": LONG,
        "created_utc": 1_700_000_000,
        "score": 10,
        "url": "https://www.reddit.com/r/Scams/comments/abc123/",
        "fetched_via": "arctic:top",
        "subreddit": "Scams",
    }
    assert isinstance(post["created_utc"], int)


def test_reddit_post_missing_title_and_body():
    post = scrape.reddit_post({"id": "z", "title": None, "created_utc": 5}, "reddit:top")
    assert post["title"] == "" and post["body"] == "" and post["score"] is None


def test_arctic_url_defaults_and_params():
    url = scrape.arctic_url(query="pig butchering", before=123)
    assert url.startswith(scrape.ARCTIC_URL + "?")
    assert query_of(url) == {
        "subreddit": "Scams", "limit": "100", "fields": scrape.ARCTIC_FIELDS,
        "query": "pig butchering", "before": "123",
    }


def test_arctic_url_params_override_defaults():
    assert query_of(scrape.arctic_url(limit=5))["limit"] == "5"


class FixedNow(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2024, 5, 15, tzinfo=tz)


def ts(year, month):
    return int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())


def test_month_windows_steps_and_stops_before_current_month(monkeypatch):
    monkeypatch.setattr(scrape, "datetime", FixedNow)
    windows = list(scrape.month_windows((2023, 11), 2))
    # Nov 2023, Jan 2024, Mar 2024; May 2024 is the current month so it is left out.
    assert windows == [
        (ts(2023, 11), ts(2023, 12)),
        (ts(2024, 1), ts(2024, 2)),
        (ts(2024, 3), ts(2024, 4)),
    ]


def test_month_windows_december_rolls_over(monkeypatch):
    monkeypatch.setattr(scrape, "datetime", FixedNow)
    assert list(scrape.month_windows((2023, 12), 12)) == [(ts(2023, 12), ts(2024, 1))]


def test_month_windows_empty_when_start_is_now(monkeypatch):
    monkeypatch.setattr(scrape, "datetime", FixedNow)
    assert list(scrape.month_windows((2024, 5), 1)) == []


@pytest.mark.parametrize("code, body, overload", [
    (422, b'{"error": "Timeout. Maybe slow down a bit"}', True),
    (422, b"TIMEOUT", True),
    (422, b'{"error": "bad parameter"}', False),
    (500, b"timeout", False),
    (404, b"", False),
])
def test_is_overload(code, body, overload):
    assert scrape.is_overload(http_error(code, body)) is overload


def test_is_overload_unreadable_body():
    class Broken(io.BytesIO):
        def read(self, *args):
            raise OSError("closed")
    err = urllib.error.HTTPError("https://x", 422, "msg", {}, Broken())
    assert scrape.is_overload(err) is False


def test_post_store_dedupes_by_id_and_keeps_order():
    store = scrape.PostStore()
    assert store.add({"id": "a", "n": 1}) is True
    assert store.add({"id": "b", "n": 2}) is True
    assert store.add({"id": "a", "n": 3}) is False
    assert len(store) == 2
    assert list(store.posts) == ["a", "b"]
    assert store.posts["a"]["n"] == 1  # the first copy wins


def test_cfpb_post_and_narrative_field_names():
    source = {"complaint_id": 42, "date_received": "2023-01-02T00:00:00Z",
              "complaint_narrative": "old field name"}
    post = scrape.cfpb_post(source, "cfpb:scam")
    assert post["id"] == "cfpb-42" and post["source_id"] == "42"
    assert post["body"] == "old field name" and post["title"] == "" and post["score"] is None
    assert post["created_utc"] == int(datetime(2023, 1, 2, tzinfo=timezone.utc).timestamp())
    assert post["url"].endswith("/42")
    assert scrape.cfpb_narrative({"complaint_what_happened": "new"}) == "new"
    assert scrape.cfpb_narrative({}) == ""


# --- Fetcher: retries and overload, with time.sleep patched -----------------


def scripted_fetcher(monkeypatch, outcomes):
    """A real Fetcher whose _request returns or raises each outcome in turn."""
    fetcher = scrape.Fetcher()
    remaining = list(outcomes)

    def fake_request(url):
        fetcher.requests += 1
        outcome = remaining.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(fetcher, "_request", fake_request)
    return fetcher


def backoffs(delays):
    """Retry delays only, leaving out the sub-second rate-limit waits."""
    return [d for d in delays if d >= 5]


def test_get_json_success(monkeypatch, sleeps):
    fetcher = scripted_fetcher(monkeypatch, [{"data": [1]}])
    assert fetcher.get_json("u") == {"data": [1]}
    assert fetcher.requests == 1 and backoffs(sleeps) == []


# Factories, not instances: an HTTPError's body can only be read once.
@pytest.mark.parametrize("make_error", [
    lambda: http_error(429), lambda: http_error(503), lambda: http_error(422, b"Timeout"),
    lambda: urllib.error.URLError("dns"), TimeoutError, lambda: json.JSONDecodeError("x", "", 0),
])
def test_get_json_retries_transient_errors(monkeypatch, sleeps, make_error):
    fetcher = scripted_fetcher(monkeypatch, [make_error(), make_error(), {"ok": True}])
    assert fetcher.get_json("u") == {"ok": True}
    assert fetcher.requests == 3
    assert backoffs(sleeps) == [5, 10]  # exponential backoff


@pytest.mark.parametrize("code, body", [(404, b""), (403, b""), (422, b"bad parameter")])
def test_get_json_gives_up_on_client_errors(monkeypatch, sleeps, code, body):
    fetcher = scripted_fetcher(monkeypatch, [http_error(code, body), {"never": "reached"}])
    assert fetcher.get_json("u") is None
    assert fetcher.requests == 1 and backoffs(sleeps) == []


def test_get_json_out_of_retries(monkeypatch, sleeps):
    fetcher = scripted_fetcher(monkeypatch, [http_error(500) for _ in range(scrape.MAX_ATTEMPTS)])
    assert fetcher.get_json("u") is None
    assert fetcher.requests == scrape.MAX_ATTEMPTS
    assert backoffs(sleeps) == [5, 10, 20, 40]  # no sleep after the last attempt


def test_get_json_without_retries_tries_once(monkeypatch, sleeps):
    fetcher = scripted_fetcher(monkeypatch, [http_error(429), {"ok": True}])
    assert fetcher.get_json("u", retries=False) is None
    assert fetcher.requests == 1


def test_get_json_api_error_field(monkeypatch, sleeps):
    fetcher = scripted_fetcher(monkeypatch, [{"error": "rate limited"}])
    assert fetcher.get_json("u") is None


def test_rate_limit_waits_between_requests(monkeypatch, sleeps):
    clock = iter([100.0, 100.0, 100.2, 100.2])
    monkeypatch.setattr(scrape.time, "monotonic", lambda: next(clock))
    fetcher = scripted_fetcher(monkeypatch, [{}, {}])
    fetcher._last_request = 0.0
    fetcher.get_json("a")
    fetcher.get_json("b")
    assert sleeps == [pytest.approx(scrape.REQUEST_INTERVAL - 0.2)]


def test_request_uses_urlopen_with_user_agent(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["request"], seen["timeout"] = request, timeout
        return io.BytesIO(b'{"data": []}')

    monkeypatch.setattr(scrape.urllib.request, "urlopen", fake_urlopen)
    fetcher = scrape.Fetcher()
    assert fetcher._request("https://example.test/x") == {"data": []}
    assert seen["request"].get_header("User-agent") == scrape.USER_AGENT
    assert seen["timeout"] == scrape.TIMEOUT
    assert fetcher.requests == 1


# --- scrapers against a fake fetcher ----------------------------------------


def test_arctic_pages_pages_backwards_with_before():
    pages = {
        None: [raw_reddit("a", created=300), raw_reddit("b", created=200)],
        "200": [raw_reddit("c", created=100)],
        "100": [],
    }
    fetcher = FakeFetcher(lambda url: {"data": pages[query_of(url).get("before")]})
    got = list(scrape.arctic_pages(fetcher, max_pages=5, query="zelle"))
    assert [[p["id"] for p in page] for page in got] == [["a", "b"], ["c"]]
    assert [query_of(u).get("before") for u in fetcher.urls] == [None, "200", "100"]


def test_arctic_pages_respects_max_pages_and_failed_fetch():
    fetcher = FakeFetcher(lambda url: {"data": [raw_reddit("a", created=10)]})
    assert len(list(scrape.arctic_pages(fetcher, max_pages=2))) == 2
    assert list(scrape.arctic_pages(FakeFetcher(lambda url: None), max_pages=3)) == []


def test_scrape_reddit_top_keeps_usable_posts():
    listing = {"data": {"children": [
        {"data": raw_reddit("a")},
        {"data": raw_reddit("b", selftext="[removed]")},
        {"data": raw_reddit("c", selftext="short")},
    ]}}
    store = scrape.PostStore()
    scrape.scrape_reddit_top(FakeFetcher(lambda url: listing), store)
    assert list(store.posts) == ["reddit-a"]
    assert store.posts["reddit-a"]["fetched_via"] == "reddit:top"


def test_scrape_reddit_top_blocked():
    store = scrape.PostStore()
    scrape.scrape_reddit_top(FakeFetcher(lambda url: None), store)
    assert len(store) == 0


def test_scrape_arctic_queries_labels_and_dedupes(monkeypatch):
    monkeypatch.setattr(scrape, "ARCTIC_QUERIES", {"marketplace": ["zelle", "overpayment"]})
    monkeypatch.setattr(scrape, "MAX_PAGES_PER_QUERY", 1)
    # Both queries return post "shared"; only the first query gets it.
    answers = {
        "zelle": [raw_reddit("shared"), raw_reddit("z1")],
        "overpayment": [raw_reddit("shared"), raw_reddit("o1", selftext="tiny")],
    }
    fetcher = FakeFetcher(lambda url: {"data": answers[query_of(url)["query"]]})
    store = scrape.PostStore()
    scrape.scrape_arctic_queries(fetcher, store)
    assert {pid: p["fetched_via"] for pid, p in store.posts.items()} == {
        "reddit-shared": "arctic:zelle", "reddit-z1": "arctic:zelle",
    }


def test_scrape_arctic_top_keeps_highest_scores_per_window(monkeypatch):
    monkeypatch.setattr(scrape, "month_windows", lambda first, step: iter([(0, 100)]))
    monkeypatch.setattr(scrape, "TOP_PER_WINDOW", 2)
    posts = [raw_reddit("low", score=1), raw_reddit("high", score=50),
             raw_reddit("mid", score=20), raw_reddit("removed", selftext="[removed]", score=999)]
    fetcher = FakeFetcher(lambda url: {"data": posts})
    store = scrape.PostStore()
    scrape.scrape_arctic_top(fetcher, store)
    assert list(store.posts) == ["reddit-high", "reddit-mid"]
    assert query_of(fetcher.urls[0])["after"] == "0"


def test_scrape_arctic_new_tops_up_to_target():
    store = scrape.PostStore()
    store.add({"id": "existing"})
    fetcher = FakeFetcher(lambda url: {"data": [raw_reddit(f"n{i}", created=100 - i)
                                                 for i in range(5)]})
    scrape.scrape_arctic_new(fetcher, store, target_total=3)
    assert len(store) == 3
    assert len(fetcher.urls) == 1


def test_scrape_arctic_new_skips_when_full():
    store = scrape.PostStore()
    store.add({"id": "a"})
    fetcher = FakeFetcher(lambda url: pytest.fail("should not fetch"))
    scrape.scrape_arctic_new(fetcher, store, target_total=1)


def cfpb_hit(cid, narrative=LONG):
    return {"_source": {"complaint_id": cid, "date_received": "2023-01-02T00:00:00Z",
                        "complaint_what_happened": narrative}}


def test_scrape_cfpb_adds_narratives():
    fetcher = FakeFetcher(lambda url: {"hits": {"hits": [cfpb_hit(1), cfpb_hit(2, "")]}})
    store = scrape.PostStore()
    scrape.scrape_cfpb(fetcher, store)
    assert list(store.posts) == ["cfpb-1"]
    assert store.posts["cfpb-1"]["fetched_via"] == "cfpb:scam"
    assert query_of(fetcher.urls[0])["has_narrative"] == "true"


def test_scrape_cfpb_stops_when_narratives_are_missing():
    fetcher = FakeFetcher(lambda url: {"hits": {"hits": [cfpb_hit(1, "")]}})
    store = scrape.PostStore()
    scrape.scrape_cfpb(fetcher, store)
    assert len(store) == 0
    assert len(fetcher.urls) == 1  # gave up after the first search term


# --- output -----------------------------------------------------------------


def test_write_posts_jsonl(tmp_path, monkeypatch):
    out = tmp_path / "raw" / "posts.jsonl"
    monkeypatch.setattr(scrape, "OUT_PATH", out)
    store = scrape.PostStore()
    store.add(scrape.reddit_post(raw_reddit("a", title="Café ☕"), "arctic:top"))
    store.add(scrape.reddit_post(raw_reddit("b"), "arctic:new"))
    scrape.write_posts(store)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["reddit-a", "reddit-b"]
    assert "Café ☕" in lines[0]  # written as UTF-8, not escaped


def test_main_end_to_end_without_network(tmp_path, monkeypatch, capsys):
    out = tmp_path / "posts.jsonl"
    monkeypatch.setattr(scrape, "OUT_PATH", out)
    monkeypatch.setattr(scrape, "ARCTIC_QUERIES", {"romance": ["dating"]})
    monkeypatch.setattr(scrape, "MAX_PAGES_PER_QUERY", 1)
    monkeypatch.setattr(scrape, "month_windows", lambda first, step: iter([(0, 100)]))
    monkeypatch.setattr(scrape, "TARGET_TOTAL", 3)

    def answer(url):
        if url.startswith(scrape.REDDIT_TOP_URL):
            return None  # Reddit blocks us
        if url.startswith(scrape.CFPB_URL):
            return {"hits": {"hits": [cfpb_hit(7)]}}
        q = query_of(url)
        if "query" in q:
            return {"data": [raw_reddit("q1", created=50)]}
        if "after" in q:
            return {"data": [raw_reddit("t1", created=60)]}
        return {"data": [raw_reddit("n1", created=40), raw_reddit("n2", created=30)]}

    fake = FakeFetcher(answer)
    monkeypatch.setattr(scrape, "Fetcher", lambda: fake)
    scrape.main()

    ids = [json.loads(line)["id"] for line in out.read_text().splitlines()]
    assert ids == ["reddit-q1", "reddit-t1", "cfpb-7"]  # already at TARGET_TOTAL
    printed = capsys.readouterr().out
    assert f"wrote 3 posts to {out}" in printed
    assert "per fetched_via:" in printed


# --- other subreddits ---------------------------------------------------------


def test_collect_subreddit_queries_split_the_quota(monkeypatch):
    def answer(url):
        q = query_of(url)
        return {"data": [raw_reddit(f"{q['query']}-{i}", created=100 - i) for i in range(5)]}

    fetcher, store = FakeFetcher(answer), scrape.PostStore()
    added = scrape.collect_subreddit(fetcher, store, "jobs", 4, ("scam", "fake"))
    assert added == 4
    assert {query_of(u)["subreddit"] for u in fetcher.urls} == {"jobs"}
    assert [p["fetched_via"] for p in store.posts.values()] == ["arctic:r/jobs:scam"] * 2 + ["arctic:r/jobs:fake"] * 2


def test_collect_subreddit_windows_keep_top_scores_newest_first(monkeypatch):
    windows = [(0, 10), (10, 20)]
    monkeypatch.setattr(scrape, "month_windows", lambda first, step: iter(windows))

    def answer(url):
        after = query_of(url)["after"]
        posts = [raw_reddit(f"w{after}-low", score=1), raw_reddit(f"w{after}-high", score=9)]
        return {"data": [{**p, "subreddit": "Romancescam"} for p in posts]}

    fetcher, store = FakeFetcher(answer), scrape.PostStore()
    assert scrape.collect_subreddit(fetcher, store, "Romancescam", 2, ()) == 2
    assert list(store.posts) == ["reddit-w10-high", "reddit-w0-high"]
    assert store.posts["reddit-w10-high"]["url"] == "https://www.reddit.com/r/Romancescam/comments/w10-high/"


def test_collect_subreddit_skips_after_failed_windows(monkeypatch):
    monkeypatch.setattr(scrape, "month_windows", lambda first, step: iter([(i, i + 1) for i in range(10)]))
    fetcher = FakeFetcher(lambda url: None)
    assert scrape.collect_subreddit(fetcher, scrape.PostStore(), "x", 50, ()) == 0
    assert len(fetcher.urls) == scrape.MAX_FAILED_WINDOWS


def test_scrape_subreddits_counts_posts_already_scraped(monkeypatch):
    monkeypatch.setattr(scrape, "SUBREDDIT_SOURCES", {"jobs": (2, ("scam",))})
    store = scrape.PostStore()
    store.add(scrape.reddit_post({**raw_reddit("old"), "subreddit": "jobs"}, "arctic:r/jobs:scam"))
    fetcher = FakeFetcher(lambda url: {"data": [raw_reddit("new1"), raw_reddit("new2")]})
    scrape.scrape_subreddits(fetcher, store)
    assert list(store.posts) == ["reddit-old", "reddit-new1"]


def test_load_posts_keeps_existing_and_backfills_subreddit(tmp_path, monkeypatch):
    out = tmp_path / "posts.jsonl"
    old = scrape.reddit_post(raw_reddit("a"), "arctic:top")
    del old["subreddit"]
    out.write_text(json.dumps(old) + "\n\n")
    monkeypatch.setattr(scrape, "OUT_PATH", out)
    store = scrape.PostStore()
    scrape.load_posts(store)
    assert store.posts["reddit-a"]["subreddit"] == "Scams"


def test_load_posts_without_file(tmp_path, monkeypatch):
    monkeypatch.setattr(scrape, "OUT_PATH", tmp_path / "missing.jsonl")
    store = scrape.PostStore()
    scrape.load_posts(store)
    assert len(store) == 0
