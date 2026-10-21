import asyncio
import json
from types import SimpleNamespace

import pytest

from pipeline import extract
from pipeline.prompts import SCAM_TYPES, STEPS, STORY_TOOL, VOCAB


def good_raw(**over):
    raw = {
        "scam_type": "pig_butchering",
        "events": [
            {"step": "initial_contact", "actor": "scammer", "detail": "Wrong-number text"},
            {"step": "large_payment", "actor": "victim", "detail": "Sent crypto"},
        ],
        "outcome": "unknown",
        "amount_lost_usd": 5000,
        "snippet": "A victim lost savings to a fake trading app.",
    }
    raw.update(over)
    return raw


class FakeClient:
    def __init__(self, responses, fail_first=0):
        self.responses = responses  # text -> raw dict
        self.calls = 0
        self.fail_first = fail_first
        self.messages = self

    async def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise RuntimeError("transient")
        text = kwargs["messages"][0]["content"]
        for key, raw in self.responses.items():
            if key in text:
                return SimpleNamespace(
                    content=[SimpleNamespace(type="tool_use", name=STORY_TOOL["name"], input=raw)]
                )
        raise AssertionError("unexpected post")


def post(i, text=None):
    return {"id": f"reddit-{i}", "source": "reddit", "text": text or f"post {i} body", "n_chars": 10}


def run(posts, client, tmp_path):
    return asyncio.run(extract.extract_posts(posts, client, cache_dir=tmp_path))


def test_vocab_loaded_from_contract():
    assert STEPS == VOCAB["steps"]
    assert STORY_TOOL["input_schema"]["properties"]["scam_type"]["enum"] == SCAM_TYPES


def test_valid_story_matches_contract(tmp_path):
    stories, dropped, _ = run([post(1)], FakeClient({"post 1": good_raw()}), tmp_path)
    assert dropped == []
    (s,) = stories
    assert set(s) == {"id", "source", "scam_type", "events", "outcome", "amount_lost_usd", "snippet"}
    assert s["id"] == "reddit-1" and s["outcome"] == "lost_money"


def test_outcome_and_null_amount_rules():
    s = extract.build_story(post(1), good_raw(amount_lost_usd=None, outcome="escaped"))
    assert s["amount_lost_usd"] is None and s["outcome"] == "escaped"
    raw = good_raw(amount_lost_usd=None, outcome="escaped")
    raw["events"].append({"step": "money_lost", "actor": "victim", "detail": "Gone"})
    assert extract.build_story(post(1), raw)["outcome"] == "lost_money"
    assert extract.build_story(post(1), good_raw(amount_lost_usd=0, outcome="escaped"))["amount_lost_usd"] is None


def test_snippet_is_anonymised():
    s = extract.build_story(post(1), good_raw(snippet="Contact scam@evil.com or call 555-123-4567."))
    assert "scam@evil.com" not in s["snippet"]
    assert "555-123-4567" not in s["snippet"]


def test_invalid_stories_are_dropped_and_counted(tmp_path, capsys):
    client = FakeClient({
        "post 1": good_raw(),
        "post 2": good_raw(scam_type="lottery"),
        "post 3": good_raw(events=[]),
        "post 4": good_raw(events=[{"step": "teleport", "actor": "scammer", "detail": "x"}]),
    })
    stories, dropped, _ = run([post(i) for i in range(1, 5)], client, tmp_path)
    assert [s["id"] for s in stories] == ["reddit-1"]
    assert [d[0] for d in dropped] == ["reddit-2", "reddit-3", "reddit-4"]
    assert all(r.startswith("invalid:") for _, r in dropped)


def test_cache_makes_reruns_free(tmp_path):
    client = FakeClient({"post 1": good_raw(), "post 2": good_raw()})
    _, _, stats = run([post(1), post(2)], client, tmp_path)
    assert stats == {"cached": 0, "api_calls": 2} and client.calls == 2
    assert extract.cache_path("reddit-1", tmp_path).exists()
    stories, _, stats = run([post(1), post(2)], client, tmp_path)
    assert stats == {"cached": 2, "api_calls": 0} and client.calls == 2
    assert len(stories) == 2


def test_cache_key_includes_prompt_hash(tmp_path, monkeypatch):
    p1 = extract.cache_path("reddit-1", tmp_path)
    monkeypatch.setattr(extract, "prompt_hash", lambda: "different")
    assert extract.cache_path("reddit-1", tmp_path) != p1


def test_retries_then_succeeds(tmp_path, monkeypatch):
    async def no_sleep(_):
        return None
    monkeypatch.setattr(extract.asyncio, "sleep", no_sleep)
    client = FakeClient({"post 1": good_raw()}, fail_first=2)
    stories, dropped, _ = run([post(1)], client, tmp_path)
    assert len(stories) == 1 and not dropped and client.calls == 3


def test_api_failure_is_dropped(tmp_path, monkeypatch):
    async def no_sleep(_):
        return None
    monkeypatch.setattr(extract.asyncio, "sleep", no_sleep)
    client = FakeClient({"post 1": good_raw()}, fail_first=99)
    stories, dropped, _ = run([post(1)], client, tmp_path)
    assert stories == [] and dropped[0][1].startswith("api error")
    assert not extract.cache_path("reddit-1", tmp_path).exists()


def test_main_writes_output(tmp_path, monkeypatch, capsys):
    inp = tmp_path / "in.jsonl"
    inp.write_text("\n".join(json.dumps(post(i)) for i in range(1, 4)) + "\n")
    out = tmp_path / "stories.json"
    client = FakeClient({"post 1": good_raw(), "post 2": good_raw(outcome="bogus", amount_lost_usd=None), "post 3": good_raw()})
    monkeypatch.setattr(extract, "load_api_key", lambda: "test")
    monkeypatch.setattr(extract, "CACHE_DIR", tmp_path / "cache")
    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: client)
    real = extract.extract_posts
    monkeypatch.setattr(extract, "extract_posts",
                        lambda posts, c, concurrency: real(posts, c, cache_dir=tmp_path / "cache", concurrency=concurrency))
    extract.main(["--input", str(inp), "--output", str(out), "--limit", "2"])
    data = json.loads(out.read_text())
    assert [s["id"] for s in data] == ["reddit-1"]
    printed = capsys.readouterr().out
    assert "dropped: 1" in printed and "reddit-2" in printed


def test_cache_only_builds_without_client(tmp_path):
    extract.cache_path("reddit-1", tmp_path).write_text(json.dumps(good_raw()))
    stories, dropped, stats = run([post(1), post(2)], None, tmp_path)
    assert [s["id"] for s in stories] == ["reddit-1"]
    assert dropped[0][0] == "reddit-2" and dropped[0][1].startswith("not in cache")
    assert stats == {"cached": 1, "api_calls": 0}


def test_main_from_cache_only_needs_no_key(tmp_path, monkeypatch, capsys):
    inp = tmp_path / "in.jsonl"
    inp.write_text(json.dumps(post(1)) + "\n" + json.dumps(post(2)) + "\n")
    out = tmp_path / "stories.json"
    cache = tmp_path / "cache"
    cache.mkdir()
    extract.cache_path("reddit-1", cache).write_text(json.dumps(good_raw()))
    monkeypatch.setattr(extract, "load_api_key", lambda: pytest.fail("key requested"))
    real = extract.extract_posts
    monkeypatch.setattr(extract, "extract_posts",
                        lambda posts, c, concurrency: real(posts, c, cache_dir=cache, concurrency=concurrency))
    extract.main(["--from-cache-only", "--input", str(inp), "--output", str(out)])
    assert [s["id"] for s in json.loads(out.read_text())] == ["reddit-1"]
    assert "dropped: 1" in capsys.readouterr().out


def test_print_cache_key(capsys):
    extract.main(["--print-cache-key", "reddit-abc/1"])
    out = capsys.readouterr().out.strip()
    assert out == str(extract.cache_path("reddit-abc/1"))
    assert out.endswith(f"reddit-abc_1-{extract.prompt_hash()}.json")
