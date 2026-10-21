import json
import os
from collections import Counter
from types import SimpleNamespace

import jsonschema
import pytest

from pipeline import matcher, prompts

GOOD = {
    "scam_type": "fake_job",
    "confidence": 0.9,
    "steps_so_far": ["initial_contact", "platform_switch"],
    "likely_next_step": "small_payment",
    "advice": "Do not pay anything to get a job.",
}

DEMO_MESSAGES = json.loads(matcher.DEMO_MESSAGES_PATH.read_text())
DEMO_RESPONSES = json.loads(matcher.DEMO_RESPONSES_PATH.read_text())
DEMO_EXPECTED = {"crypto_mentor": "pig_butchering", "fake_job": "fake_job"}


def demo_text(demo_id: str) -> str:
    return next(m["text"] for m in DEMO_MESSAGES if m["id"] == demo_id)


class FakeClient:
    """Stands in for anthropic.Anthropic: messages.create returns one tool_use block."""

    def __init__(self, payload, name=matcher.TOOL_NAME):
        self.payload = payload
        self.name = name
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(type="tool_use", id="toolu_test", name=self.name, input=self.payload)
        return SimpleNamespace(content=[block], stop_reason="tool_use")


class TextOnlyClient(FakeClient):
    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="I think this is a scam.")])


class BrokenClient:
    def __init__(self):
        self.calls = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls += 1
        raise ConnectionError("no network")


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    """Every test gets an empty cache and no API key, so nothing can reach the network."""
    monkeypatch.setattr(matcher, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(matcher, "_load_api_key", lambda: None)
    monkeypatch.setattr(matcher, "_load_typesafe_key", lambda: None)
    return tmp_path / "cache"


# Vocabulary comes from prompts.py


def test_vocab_is_shared_with_prompts():
    assert matcher.STEPS is prompts.STEPS
    assert matcher.SCAM_TYPES is prompts.SCAM_TYPES
    assert matcher.VOCAB is prompts.VOCAB
    assert matcher.STEP_GUIDE is prompts.STEP_GUIDE
    assert matcher.SCAM_TYPE_GUIDE is prompts.SCAM_TYPE_GUIDE
    assert not hasattr(matcher, "STEP_DESCRIPTIONS")
    assert not hasattr(matcher, "SCAM_TYPE_DESCRIPTIONS")


def test_vocab_matches_contract_schema():
    props = matcher.SCHEMA["properties"]
    assert props["scam_type"]["enum"] == prompts.SCAM_TYPES
    assert props["likely_next_step"]["enum"] == prompts.STEPS
    assert props["steps_so_far"]["items"]["enum"] == prompts.STEPS


def test_prompt_and_tool_use_vocab():
    for step in prompts.STEPS:
        assert f"- {step}: {prompts.STEP_GUIDE[step]}" in matcher.SYSTEM_PROMPT
    for scam_type in prompts.SCAM_TYPES:
        assert f"- {scam_type}: {prompts.SCAM_TYPE_GUIDE[scam_type]}" in matcher.SYSTEM_PROMPT
    props = matcher.TOOL["input_schema"]["properties"]
    assert props["scam_type"]["enum"] is prompts.SCAM_TYPES
    assert props["likely_next_step"]["enum"] is prompts.STEPS
    assert props["steps_so_far"]["items"]["enum"] is prompts.STEPS
    assert matcher.TOOL["input_schema"]["required"] == matcher.SCHEMA["required"]


def test_rule_tables_use_known_vocab():
    assert set(matcher.STEP_CUES) <= set(prompts.STEPS)
    assert set(matcher.SCAM_TYPE_CUES) <= set(prompts.SCAM_TYPES)
    assert set(matcher.ADVICE) == set(prompts.SCAM_TYPES)
    assert set(matcher.DEFAULT_NEXT) == set(prompts.STEPS)
    assert set(matcher.DEFAULT_NEXT.values()) <= set(prompts.STEPS)


# API path (mocked client)


def test_api_path_returns_tool_output():
    client = FakeClient(GOOD)
    result, source = matcher.match_with_source("hello job offer", client=client)
    assert (result, source) == (GOOD, "api")
    call = client.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["tool_choice"] == {"type": "tool", "name": matcher.TOOL_NAME}
    assert call["tools"] == [matcher.TOOL]
    assert call["system"] == matcher.SYSTEM_PROMPT
    assert "hello job offer" in call["messages"][0]["content"]


def test_api_path_used_when_key_set(monkeypatch):
    client = FakeClient(GOOD)
    monkeypatch.setattr(matcher, "_load_api_key", lambda: "test-key-not-real")
    monkeypatch.setattr(matcher, "_make_client", lambda: client)
    assert matcher.match_with_source("some message") == (GOOD, "api")
    assert len(client.calls) == 1


def test_api_path_tried_first_for_demo_messages():
    client = FakeClient(GOOD)
    assert matcher.match_with_source(demo_text("fake_job"), client=client) == (GOOD, "api")


def test_cache_hit_skips_client(offline):
    client = FakeClient(GOOD)
    matcher.match("cache me", client=client)
    assert len(list(offline.glob("*.json"))) == 1
    broken = BrokenClient()
    assert matcher.match_with_source("cache me", client=broken) == (GOOD, "cache")
    assert len(client.calls) == 1
    assert broken.calls == 0


def test_use_cache_false_skips_cache(offline):
    client = FakeClient(GOOD)
    matcher.match("no cache", client=client, use_cache=False)
    matcher.match("no cache", client=client, use_cache=False)
    assert len(client.calls) == 2
    assert not offline.exists()


# Fallbacks


def test_no_key_uses_rules_without_building_client(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def no_client():
        raise AssertionError("client must not be built without a key")

    monkeypatch.setattr(matcher, "_make_client", no_client)
    result, source = matcher.match_with_source("Hello, I'm Tom from your bank fraud department.")
    assert source == "rules"
    assert result["scam_type"] == "impersonation"
    matcher.validate(result)


def test_api_exception_falls_back_to_rules(offline):
    broken = BrokenClient()
    result, source = matcher.match_with_source("Hello, I'm Tom from your bank fraud department.", client=broken)
    assert broken.calls == 1
    assert source == "rules"
    assert result["scam_type"] == "impersonation"
    matcher.validate(result)
    assert not offline.exists()


@pytest.mark.parametrize(
    "bad",
    [
        {**GOOD, "scam_type": "lottery"},
        {**GOOD, "confidence": 1.5},
        {**GOOD, "steps_so_far": ["made_up_step"]},
        {**GOOD, "likely_next_step": "made_up_step"},
        {**GOOD, "advice": ""},
        {k: v for k, v in GOOD.items() if k != "advice"},
        {**GOOD, "extra": 1},
        "not a dict",
    ],
)
def test_invalid_tool_output_falls_back_to_rules(bad, offline):
    client = FakeClient(bad)
    result, source = matcher.match_with_source("Hello, I'm Tom from your bank fraud department.", client=client)
    assert len(client.calls) == 1
    assert source == "rules"
    matcher.validate(result)
    assert not offline.exists() or not list(offline.glob("*.json"))


@pytest.mark.parametrize("client", [TextOnlyClient(GOOD), FakeClient(GOOD, name="other_tool")])
def test_missing_tool_use_block_falls_back(client):
    result, source = matcher.match_with_source("Is this Daniel? Wrong number, I trade crypto.", client=client)
    assert source == "rules"
    matcher.validate(result)


@pytest.mark.parametrize("demo_id", sorted(DEMO_EXPECTED))
def test_demo_messages_fall_back_to_stored_responses(demo_id):
    text = demo_text(demo_id)
    assert matcher.match_with_source(text) == (DEMO_RESPONSES[demo_id], "demo")
    padded = "  " + text.replace("\n", "\n\n") + "\n"
    assert matcher.match_with_source(padded, client=BrokenClient()) == (DEMO_RESPONSES[demo_id], "demo")
    assert matcher.match_with_source(text, client=FakeClient({**GOOD, "confidence": 3})) == (
        DEMO_RESPONSES[demo_id],
        "demo",
    )


def test_demo_files_valid():
    assert {m["id"] for m in DEMO_MESSAGES} == set(DEMO_RESPONSES) == set(DEMO_EXPECTED)
    for demo_id, response in DEMO_RESPONSES.items():
        matcher.validate(response)
        assert response["scam_type"] == DEMO_EXPECTED[demo_id]


def test_corrupt_cache_entry_ignored(offline):
    offline.mkdir(parents=True)
    matcher._cache_path("cached text").write_text(json.dumps({**GOOD, "confidence": 9}))
    result, source = matcher.match_with_source("cached text", client=FakeClient(GOOD))
    assert (result, source) == (GOOD, "api")


def test_empty_text_rejected():
    with pytest.raises(ValueError):
        matcher.match("   ", client=FakeClient(GOOD))


# Offline rule-based matcher


@pytest.mark.parametrize("demo_id", sorted(DEMO_EXPECTED))
def test_rule_match_demo_messages_schema_valid(demo_id):
    result = matcher.rule_match(demo_text(demo_id))
    jsonschema.validate(result, matcher.SCHEMA)
    assert result["scam_type"] == DEMO_EXPECTED[demo_id]
    assert result["confidence"] >= 0.6
    steps = result["steps_so_far"]
    assert "initial_contact" in steps and "platform_switch" in steps
    assert steps.index("initial_contact") < steps.index("platform_switch")
    assert len(steps) == len(set(steps))
    assert result["likely_next_step"] not in steps or result["likely_next_step"] == "small_payment"


def test_rule_match_non_demo_text():
    text = (
        "Hey this is Jake, saw your listing for the PS5 on Facebook Marketplace. "
        "I'm out of town so my courier will do pickup. I sent $50 extra by mistake, "
        "can you refund it to my Zelle?"
    )
    result = matcher.match(text)
    assert result["scam_type"] == "marketplace"
    assert result["advice"] == matcher.ADVICE["marketplace"]


def test_rule_match_harmless_text_low_confidence():
    result = matcher.rule_match("See you at lunch tomorrow at noon.")
    assert result["scam_type"] == "other"
    assert result["confidence"] <= 0.3
    matcher.validate(result)


def test_next_step_transitions():
    table = {("fake_job", "platform_switch"): Counter({"small_payment": 3, "trust_building": 1})}
    assert matcher.next_step("fake_job", ["initial_contact", "platform_switch"], table) == "small_payment"
    assert matcher.next_step("romance", ["authority_claim"], {}) == matcher.DEFAULT_NEXT["authority_claim"]
    assert matcher.next_step("other", [], {}) == "initial_contact"


def test_load_transitions_from_real_stories():
    table = matcher.load_transitions()
    for (scam_type, step), counts in table.items():
        assert scam_type is None or scam_type in prompts.SCAM_TYPES
        assert step in prompts.STEPS
        assert set(counts) <= set(prompts.STEPS)


# Jev path (TypeSafe System One), all with fake clients


def jev_response(scam_type="fake_job", p_type=0.8, p_scam=0.9, seen=(), p_seen=0.9):
    from typesafe_sdk import ChoiceAnswer, NoulAnswer

    others = (1 - p_type) / (len(prompts.SCAM_TYPES) - 1)
    probabilities = {t: (p_type if t == scam_type else others) for t in prompts.SCAM_TYPES}
    nouls = {"is_scam": NoulAnswer(type="noul", noul=p_scam)}
    for step in prompts.STEPS:
        nouls[f"step:{step}"] = NoulAnswer(type="noul", noul=p_seen if step in seen else 0.05)
    choice = ChoiceAnswer(type="choice", choice=scam_type, confidence=p_type, probabilities=probabilities)
    return SimpleNamespace(choices={"scam_type": choice}, nouls=nouls)


class FakeJev:
    """Stands in for typesafe_sdk.TypeSafeClient: system_one returns a fixed response."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def system_one(self, **kwargs):
        self.calls.append(kwargs)
        return self.response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class BrokenJev(FakeJev):
    def __init__(self, exc=ConnectionError("no network")):
        super().__init__(None)
        self.exc = exc

    def system_one(self, **kwargs):
        self.calls.append(kwargs)
        raise self.exc


def test_jev_questions_cover_vocab():
    from typesafe_sdk import Choice, Noul

    questions = matcher.jev_questions()
    assert set(questions) == {"scam_type", "is_scam"} | {f"step:{s}" for s in prompts.STEPS}
    assert isinstance(questions["scam_type"], Choice)
    assert list(questions["scam_type"].criteria) == prompts.SCAM_TYPES
    for step in prompts.STEPS:
        question = questions[f"step:{step}"]
        assert isinstance(question, Noul)
        assert prompts.STEP_GUIDE[step] in question.instructions


def test_jev_is_tried_before_sonnet():
    jev = FakeJev(jev_response(seen=("initial_contact", "platform_switch")))
    sonnet = FakeClient(GOOD)
    result, source = matcher.match_with_source("Hi, we found your resume. Add me on Telegram.", client=sonnet, jev_client=jev)
    assert source == "jev"
    assert sonnet.calls == []
    jsonschema.validate(result, matcher.SCHEMA)
    assert result["scam_type"] == "fake_job"
    assert set(result["steps_so_far"]) == {"initial_contact", "platform_switch"}
    call = jev.calls[0]
    assert call["model"] == matcher.JEV_MODEL
    assert call["state"]["pasted_text"].startswith("Hi, we found your resume")
    assert set(call["questions"]) == set(matcher.jev_questions())


def test_jev_result_orders_steps_by_story_position():
    response = jev_response(seen=("small_payment", "initial_contact", "trust_building"))
    positions = {"initial_contact": 0.0, "trust_building": 0.3, "small_payment": 0.6}
    result = matcher.jev_result(response, transitions={}, positions=positions)
    assert result["steps_so_far"] == ["initial_contact", "trust_building", "small_payment"]
    assert result["likely_next_step"] == matcher.DEFAULT_NEXT["small_payment"]


def test_jev_result_uses_story_transitions_for_next_step():
    response = jev_response(scam_type="pig_butchering", seen=("initial_contact",))
    transitions = {("pig_butchering", "initial_contact"): Counter({"platform_switch": 5, "trust_building": 2})}
    result = matcher.jev_result(response, transitions=transitions, positions={})
    assert result["likely_next_step"] == "platform_switch"


def test_jev_confidence_is_type_probability_times_scam_probability():
    result = matcher.jev_result(jev_response(p_type=0.8, p_scam=0.9), transitions={}, positions={})
    assert result["confidence"] == 0.72
    assert result["advice"] == matcher.ADVICE["fake_job"]


def test_jev_harmless_text_is_other_with_low_confidence():
    result = matcher.jev_result(jev_response(scam_type="romance", p_scam=0.1), transitions={}, positions={})
    assert result["scam_type"] == "other"
    assert result["confidence"] == 0.1
    assert result["advice"] == matcher.LOW_RISK_ADVICE
    jsonschema.validate(result, matcher.SCHEMA)


def test_jev_steps_below_threshold_are_not_seen():
    response = jev_response(seen=("platform_switch",), p_seen=matcher.NOUL_THRESHOLD)
    assert matcher.jev_result(response, transitions={}, positions={})["steps_so_far"] == []


def test_jev_failure_falls_back_to_sonnet():
    jev = BrokenJev()
    result, source = matcher.match_with_source("some text", client=FakeClient(GOOD), jev_client=jev)
    assert source == "api"
    assert result == GOOD
    assert len(jev.calls) == 1


@pytest.mark.parametrize("demo_id", sorted(DEMO_EXPECTED))
def test_jev_failure_without_sonnet_falls_back_to_demo(demo_id):
    _, source = matcher.match_with_source(demo_text(demo_id), jev_client=BrokenJev())
    assert source == "demo"


def test_jev_missing_answer_falls_back_to_rules():
    response = jev_response()
    del response.nouls["is_scam"]
    result, source = matcher.match_with_source("Send $50 registration fee on Telegram", jev_client=FakeJev(response))
    assert source == "rules"
    jsonschema.validate(result, matcher.SCHEMA)


def test_jev_invalid_answer_falls_back_to_rules():
    response = jev_response()
    response.choices["scam_type"] = response.choices["scam_type"].model_copy(update={"choice": "not_a_scam_type"})
    _, source = matcher.match_with_source("Send $50 registration fee on Telegram", jev_client=FakeJev(response))
    assert source == "rules"


def test_jev_key_set_builds_client_and_caches(monkeypatch, offline):
    jev = FakeJev(jev_response(seen=("initial_contact",)))
    built = []
    monkeypatch.setattr(matcher, "_load_typesafe_key", lambda: "test-key-not-real")
    monkeypatch.setattr(matcher, "_make_jev_client", lambda: built.append(1) or jev)
    first, source = matcher.match_with_source("Hello, is this Anna?")
    assert source == "jev" and built == [1]
    again, source = matcher.match_with_source("Hello, is this Anna?")
    assert source == "cache" and again == first
    assert len(jev.calls) == 1


def test_no_typesafe_key_never_builds_jev_client(monkeypatch):
    def boom():
        raise AssertionError("Jev client built without a key")

    monkeypatch.setattr(matcher, "_make_jev_client", boom)
    _, source = matcher.match_with_source("Send $50 registration fee on Telegram")
    assert source == "rules"


def test_make_jev_client_requires_key():
    with pytest.raises(RuntimeError, match="TYPESAFE_API_KEY"):
        matcher._make_jev_client()


def test_env_key_reads_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(matcher, "ROOT", tmp_path)
    (tmp_path / ".env").write_text('ANTHROPIC_API_KEY=a\nexport TYPESAFE_API_KEY="t-test"\n')
    assert matcher._env_key("TYPESAFE_API_KEY") == "t-test"
    assert matcher._env_key("MISSING_KEY") is None


def test_load_step_positions_from_real_stories():
    positions = matcher.load_step_positions()
    assert positions and set(positions) <= set(prompts.STEPS)
    assert all(0 <= p <= 1 for p in positions.values())
    assert positions["initial_contact"] < positions["money_lost"]


# Live check against the real API. Skipped unless a key is set; run it with
#   ANTHROPIC_API_KEY=... python -m pytest -q pipeline/tests/test_matcher.py -k live


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
@pytest.mark.parametrize("demo_id", sorted(DEMO_EXPECTED))
def test_live_sonnet_on_demo_messages(demo_id):
    import anthropic

    result, source = matcher.match_with_source(demo_text(demo_id), client=anthropic.Anthropic(), use_cache=False)
    assert source == "api"
    jsonschema.validate(result, matcher.SCHEMA)
    assert result["scam_type"] == DEMO_EXPECTED[demo_id]
    assert result["steps_so_far"]


# Live Jev check. Skipped unless TYPESAFE_API_KEY is set in the environment; run it with
#   TYPESAFE_API_KEY=... python -m pytest -q pipeline/tests/test_matcher.py -k live


@pytest.mark.skipif(not os.environ.get("TYPESAFE_API_KEY"), reason="TYPESAFE_API_KEY not set")
@pytest.mark.parametrize("demo_id", sorted(DEMO_EXPECTED))
def test_live_jev_on_demo_messages(demo_id):
    from typesafe_sdk import TypeSafeClient

    with TypeSafeClient(model=matcher.JEV_MODEL) as client:
        result, source = matcher.match_with_source(demo_text(demo_id), use_cache=False, jev_client=client)
    assert source == "jev"
    jsonschema.validate(result, matcher.SCHEMA)
    assert result["scam_type"] == DEMO_EXPECTED[demo_id]
    assert result["steps_so_far"]
