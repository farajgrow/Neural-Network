"""Tests for pipeline.extract and pipeline.prompts. The API is always mocked."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from pipeline import extract
from pipeline.prompts import (
    SCAM_TYPE_GUIDE,
    STEP_GUIDE,
    UNSUPPORTED_KEYWORDS,
    extraction_output_schema,
    load_vocab,
    stories_file_validator,
    to_api_schema,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures" / "clean_posts.jsonl"

GOOD_OUTPUT = {
    "scam_type": "pig_butchering",
    "events": [
        {"step": "initial_contact", "actor": "scammer", "detail": "Wrong-number text about golf"},
        {"step": "small_payment", "actor": "victim", "detail": "Deposited $1,500"},
        {"step": "money_lost", "actor": "victim", "detail": "Site locked the account"},
    ],
    "outcome": "lost_money",
    "amount_lost_usd": 38000,
    "snippet": "A wrong-number text became a fake crypto platform that kept the victim's savings.",
}


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


def text_response(output, stop_reason="end_turn"):
    body = output if isinstance(output, str) else json.dumps(output)
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=body)])


class FakeClient:
    """Stands in for AsyncAnthropic. ``handler(user_message)`` returns a response or raises."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.handler(kwargs["messages"][0]["content"])
        if asyncio.iscoroutine(result):
            result = await result
        return result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def always(output):
    return lambda _message: text_response(output)


def fail_if_called(_message):
    raise AssertionError("API should not be called")


def connection_error(_message):
    raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))


def post(post_id="reddit-abc", text="A long scam story.", source="reddit"):
    return {"id": post_id, "source": source, "text": text, "n_chars": len(text)}


def run(posts, client, cache_dir, concurrency=4):
    return asyncio.run(extract.extract_all(posts, client, cache_dir, concurrency))


# --------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------


def test_guides_match_contract_vocab():
    vocab = load_vocab()
    assert list(STEP_GUIDE) == vocab["steps"]
    assert list(SCAM_TYPE_GUIDE) == vocab["scam_types"]


def test_output_schema_is_story_without_id_and_source():
    schema = extraction_output_schema()
    assert "id" not in schema["properties"] and "source" not in schema["properties"]
    assert set(schema["required"]) == {"scam_type", "events", "outcome", "amount_lost_usd", "snippet"}


def test_output_schema_has_no_unsupported_keywords_or_refs():
    blob = json.dumps(extraction_output_schema())
    for keyword in UNSUPPORTED_KEYWORDS | {"$ref", "$defs"}:
        assert f'"{keyword}"' not in blob


def test_to_api_schema_turns_nullable_type_into_anyof():
    out = to_api_schema({"type": ["number", "null"], "minimum": 0})
    assert out == {"anyOf": [{"type": "number"}, {"type": "null"}]}


def test_committed_mock_stories_pass_the_validator():
    stories = json.loads((REPO_ROOT / "data" / "stories.json").read_text())
    stories_file_validator().validate(stories)


# --------------------------------------------------------------------------
# build_story and validation
# --------------------------------------------------------------------------


def test_build_story_takes_id_and_source_from_post():
    story = extract.build_story(post("cfpb-42", source="cfpb"), GOOD_OUTPUT)
    assert story["id"] == "cfpb-42" and story["source"] == "cfpb"
    assert extract.validation_errors(story) == []


def test_build_story_scrubs_pii_the_model_copied():
    output = {**GOOD_OUTPUT, "snippet": "The scammer emailed from crook@example.com and the victim lost money."}
    output["events"] = [{"step": "initial_contact", "actor": "scammer", "detail": "Texted from +1 415 555 0134"}]
    story = extract.build_story(post(), output)
    assert "crook@example.com" not in story["snippet"]
    assert "555" not in story["events"][0]["detail"]


@pytest.mark.parametrize(
    "change",
    [
        {"scam_type": "crypto"},
        {"outcome": "won"},
        {"amount_lost_usd": -5},
        {"amount_lost_usd": "38000"},
        {"events": []},
        {"events": [{"step": "teleported", "actor": "scammer", "detail": "x"}]},
        {"events": [{"step": "initial_contact", "actor": "bot", "detail": "x"}]},
        {"events": [{"step": "initial_contact", "actor": "scammer", "detail": ""}]},
        {"snippet": ""},
        {"extra_field": 1},
    ],
)
def test_validation_rejects_contract_violations(change):
    story = extract.build_story(post(), {**GOOD_OUTPUT, **change})
    assert extract.validation_errors(story)


def test_validation_rejects_missing_field():
    output = {k: v for k, v in GOOD_OUTPUT.items() if k != "snippet"}
    assert extract.validation_errors(extract.build_story(post(), output))


# --------------------------------------------------------------------------
# extract_all: statuses and caching
# --------------------------------------------------------------------------


def test_valid_output_is_ok_and_cached(tmp_path):
    client = FakeClient(always(GOOD_OUTPUT))
    [result] = run([post()], client, tmp_path)
    assert result.status == "ok" and not result.cached
    assert result.story["id"] == "reddit-abc"
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert len(client.calls) == 1


def test_rerun_is_served_from_cache_without_api_calls(tmp_path):
    run([post()], FakeClient(always(GOOD_OUTPUT)), tmp_path)
    client = FakeClient(fail_if_called)
    [result] = run([post()], client, tmp_path)
    assert result.status == "ok" and result.cached
    assert client.calls == []


def test_changed_text_misses_the_cache(tmp_path):
    run([post(text="first version")], FakeClient(always(GOOD_OUTPUT)), tmp_path)
    client = FakeClient(always(GOOD_OUTPUT))
    run([post(text="second version")], client, tmp_path)
    assert len(client.calls) == 1


def test_changed_prompt_misses_the_cache(tmp_path, monkeypatch):
    run([post()], FakeClient(always(GOOD_OUTPUT)), tmp_path)
    monkeypatch.setattr(extract, "PROMPT_HASH", "a-different-prompt")
    client = FakeClient(always(GOOD_OUTPUT))
    run([post()], client, tmp_path)
    assert len(client.calls) == 1


def test_cache_key_contains_post_id(tmp_path):
    path = extract.cache_path(tmp_path, post("reddit-x/y"))
    assert path.name.startswith("reddit-x_y-") and path.parent == tmp_path


def test_invalid_output_is_dropped_and_not_cached(tmp_path):
    [result] = run([post()], FakeClient(always({**GOOD_OUTPUT, "scam_type": "crypto"})), tmp_path)
    assert result.status == "invalid" and result.story is None
    assert "scam_type" in result.error
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "response",
    [text_response("not json at all"), text_response(GOOD_OUTPUT, stop_reason="refusal"),
     text_response(GOOD_OUTPUT, stop_reason="max_tokens"), text_response("[1, 2]")],
)
def test_unusable_model_answers_count_as_invalid(tmp_path, response):
    [result] = run([post()], FakeClient(lambda _m: response), tmp_path)
    assert result.status == "invalid"
    assert list(tmp_path.iterdir()) == []


def test_api_error_is_counted_and_retried_next_run(tmp_path):
    [result] = run([post()], FakeClient(connection_error), tmp_path)
    assert result.status == "api_error" and "APIConnectionError" in result.error
    assert list(tmp_path.iterdir()) == []

    client = FakeClient(always(GOOD_OUTPUT))
    [result] = run([post()], client, tmp_path)
    assert result.status == "ok" and len(client.calls) == 1


def test_results_keep_input_order_and_mix_statuses(tmp_path):
    def handler(message):
        if "bad" in message:
            return text_response({**GOOD_OUTPUT, "outcome": "won"})
        if "down" in message:
            connection_error(message)
        return text_response(GOOD_OUTPUT)

    posts = [post("reddit-1", "good"), post("reddit-2", "bad"), post("reddit-3", "down"), post("reddit-4", "good too")]
    results = run(posts, FakeClient(handler), tmp_path)
    assert [r.post_id for r in results] == ["reddit-1", "reddit-2", "reddit-3", "reddit-4"]
    assert extract.summarise(results) == {"ok": 2, "invalid": 1, "api_error": 1}


def test_concurrency_is_bounded(tmp_path):
    in_flight = 0
    peak = 0

    async def handler(_message):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return text_response(GOOD_OUTPUT)

    posts = [post(f"reddit-{i}", f"story {i}") for i in range(20)]
    results = run(posts, FakeClient(handler), tmp_path, concurrency=3)
    assert all(r.status == "ok" for r in results)
    assert peak == 3


def test_request_uses_structured_output_and_haiku(tmp_path):
    client = FakeClient(always(GOOD_OUTPUT))
    run([post(text="the post body")], client, tmp_path)
    [call] = client.calls
    assert call["model"] == "claude-haiku-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "the post body" in call["messages"][0]["content"]


# --------------------------------------------------------------------------
# main(): the CLI end to end on the fixture
# --------------------------------------------------------------------------


@pytest.fixture
def fake_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def install(handler):
        client = FakeClient(handler)
        monkeypatch.setattr(extract.anthropic, "AsyncAnthropic", lambda **_kw: client)
        return client

    return install


def test_fixture_posts_match_clean_format():
    posts = extract.read_posts(FIXTURE)
    assert len(posts) == 5
    for p in posts:
        assert set(p) == {"id", "source", "text", "n_chars"}
        assert p["n_chars"] == len(p["text"])
        assert p["id"].startswith(f"{p['source']}-")


def test_main_writes_valid_stories_file(tmp_path, fake_anthropic, capsys):
    fake_anthropic(always(GOOD_OUTPUT))
    out = tmp_path / "stories.json"
    code = extract.main(["--input", str(FIXTURE), "--output", str(out),
                         "--cache-dir", str(tmp_path / "cache"), "--limit", "3"])
    assert code == 0
    stories = json.loads(out.read_text())
    stories_file_validator().validate(stories)
    assert [s["id"] for s in stories] == [p["id"] for p in extract.read_posts(FIXTURE, 3)]
    assert re.search(r"^\s*ok\s+3$", capsys.readouterr().out, re.M)


def test_main_leaves_output_untouched_when_nothing_is_valid(tmp_path, fake_anthropic):
    fake_anthropic(connection_error)
    out = tmp_path / "stories.json"
    out.write_text("[]\n")
    code = extract.main(["--input", str(FIXTURE), "--output", str(out), "--cache-dir", str(tmp_path / "cache")])
    assert code == 1
    assert out.read_text() == "[]\n"


def test_main_needs_a_key_only_for_uncached_posts(tmp_path, fake_anthropic, monkeypatch):
    args = ["--input", str(FIXTURE), "--output", str(tmp_path / "stories.json"),
            "--cache-dir", str(tmp_path / "cache"), "--limit", "2"]
    fake_anthropic(always(GOOD_OUTPUT))
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert extract.main(args) == 2

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert extract.main(args) == 0

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    fake_anthropic(fail_if_called)
    assert extract.main(args) == 0
