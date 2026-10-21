import json
from types import SimpleNamespace

import jsonschema
import pytest

from pipeline import matcher

GOOD = {
    "scam_type": "fake_job",
    "confidence": 0.9,
    "steps_so_far": ["initial_contact", "platform_switch"],
    "likely_next_step": "small_payment",
    "advice": "Do not pay anything to get a job.",
}


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(type="tool_use", name=matcher.TOOL_NAME, input=self.payload)
        return SimpleNamespace(content=[block])


class BrokenClient:
    def __init__(self):
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        raise ConnectionError("no network")


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(matcher, "CACHE_DIR", tmp_path / "cache")
    return tmp_path / "cache"


def test_match_returns_valid_result():
    client = FakeClient(GOOD)
    assert matcher.match("hello job offer", client=client) == GOOD
    call = client.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["tool_choice"] == {"type": "tool", "name": matcher.TOOL_NAME}


def test_prompt_and_tool_use_vocab():
    for step in matcher.VOCAB["steps"]:
        assert step in matcher.SYSTEM_PROMPT
    props = matcher.TOOL["input_schema"]["properties"]
    assert props["scam_type"]["enum"] == matcher.VOCAB["scam_types"]
    assert props["likely_next_step"]["enum"] == matcher.VOCAB["steps"]


@pytest.mark.parametrize(
    "bad",
    [
        {**GOOD, "scam_type": "lottery"},
        {**GOOD, "confidence": 1.5},
        {**GOOD, "steps_so_far": ["made_up_step"]},
        {**GOOD, "advice": ""},
        {k: v for k, v in GOOD.items() if k != "advice"},
        {**GOOD, "extra": 1},
    ],
)
def test_invalid_model_output_rejected(bad):
    with pytest.raises(jsonschema.ValidationError):
        matcher.match("some unseen text", client=FakeClient(bad))


def test_cache_hit_skips_client(tmp_cache):
    client = FakeClient(GOOD)
    matcher.match("cache me", client=client)
    assert len(list(tmp_cache.glob("*.json"))) == 1
    again = matcher.match("cache me", client=BrokenClient())
    assert again == GOOD
    assert len(client.calls) == 1


def test_invalid_output_not_cached(tmp_cache):
    with pytest.raises(jsonschema.ValidationError):
        matcher.match("bad", client=FakeClient({**GOOD, "confidence": 2}))
    assert not tmp_cache.exists() or not list(tmp_cache.glob("*.json"))


def test_demo_files_valid():
    messages = json.loads(matcher.DEMO_MESSAGES_PATH.read_text())
    responses = json.loads(matcher.DEMO_RESPONSES_PATH.read_text())
    assert {m["id"] for m in messages} == set(responses) == {"crypto_mentor", "fake_job"}
    for r in responses.values():
        matcher.validate(r)


def test_demo_fallback_without_network(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(matcher, "_load_api_key", lambda: None)
    responses = json.loads(matcher.DEMO_RESPONSES_PATH.read_text())
    for m in json.loads(matcher.DEMO_MESSAGES_PATH.read_text()):
        assert matcher.match(m["text"]) == responses[m["id"]]
        assert matcher.match("  " + m["text"] + "\n", client=BrokenClient()) == responses[m["id"]]


def test_no_fallback_for_unknown_text(monkeypatch):
    monkeypatch.setattr(matcher, "_load_api_key", lambda: None)
    with pytest.raises(RuntimeError):
        matcher.match("random text with no key")


def test_empty_text_rejected():
    with pytest.raises(ValueError):
        matcher.match("   ", client=FakeClient(GOOD))
