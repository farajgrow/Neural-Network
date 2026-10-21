"""Live matcher: classify a pasted message or conversation against the scam script vocabulary.

Usage: python -m pipeline.matcher "text"
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import jsonschema

from pipeline.prompts import SCAM_TYPE_GUIDE, SCAM_TYPES, STEP_GUIDE, STEPS, VOCAB

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_DIR = Path(__file__).resolve().parent
CONTRACT_DIR = ROOT / "contract"
CACHE_DIR = PIPELINE_DIR / ".cache" / "matcher"
DEMO_MESSAGES_PATH = PIPELINE_DIR / "demo_messages.json"
DEMO_RESPONSES_PATH = PIPELINE_DIR / "demo_responses.json"
STORIES_PATH = ROOT / "data" / "stories.json"

MODEL = "claude-sonnet-5-5"
TOOL_NAME = "report_match"
JEV_MODEL = "jev-latest"
JEV_TIMEOUT = 15.0
NOUL_THRESHOLD = 0.5  # a step counts as seen, and the text as a scam, above this probability
NEXT_STEP_MIN_PROBABILITY = 0.05  # below this, Jev's next-step pick gives way to the story transitions

SCHEMA = json.loads((CONTRACT_DIR / "matcher.schema.json").read_text())


def _build_system_prompt() -> str:
    steps = "\n".join(f"- {s}: {STEP_GUIDE[s]}" for s in STEPS)
    types = "\n".join(f"- {t}: {SCAM_TYPE_GUIDE[t]}" for t in SCAM_TYPES)
    return (
        "You analyse messages or conversations that a person received and match them against known scam scripts.\n"
        "Scams are modelled as sequences of steps from this fixed vocabulary:\n"
        f"{steps}\n\n"
        "Scam types:\n"
        f"{types}\n\n"
        "Instructions:\n"
        "- steps_so_far: the steps visible in the text, in the order they happen. Only include steps with evidence in the text.\n"
        "- likely_next_step: the single step most likely to come next if the person keeps engaging.\n"
        "- confidence: 0 to 1, how confident you are that this is the scam type you chose. Use low values if the text looks harmless.\n"
        "- advice: one or two plain sentences telling the person what to do now.\n"
        f"Always answer by calling the {TOOL_NAME} tool."
    )


SYSTEM_PROMPT = _build_system_prompt()

TOOL = {
    "name": TOOL_NAME,
    "description": "Report which scam script the text matches, the steps seen so far and the likely next step.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": SCHEMA["required"],
        "properties": {
            "scam_type": {"type": "string", "enum": SCAM_TYPES},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "steps_so_far": {"type": "array", "items": {"type": "string", "enum": STEPS}},
            "likely_next_step": {"type": "string", "enum": STEPS},
            "advice": {"type": "string", "minLength": 1},
        },
    },
}


def validate(result: dict) -> dict:
    jsonschema.validate(result, SCHEMA)
    return result

# Rule-based matcher (offline default)

STEP_CUES = {
    "initial_contact": [r"\bwrong (number|person)\b", r"\bis this \w+", r"\bsorry,? (who|is this)", r"\byour (resume|cv|profile) (was|has been)", r"\b(this is|i'?m) \w+ from\b", r"\bhi\b|\bhello\b", r"\brecommended to us\b", r"\bmatched\b"],
    "platform_switch": [r"\b(whatsapp|telegram|signal|wechat|line app|kik)\b", r"\b(move|switch|talk|chat|continue) (to|on|over)\b", r"\badd (me|our|my|him|her)\b.*\bon\b"],
    "trust_building": [r"\bnice to meet you\b", r"\bmentor\b", r"\bmy (uncle|aunt|father|mother|family)\b", r"\bfriend\b", r"\btrust me\b", r"\bdear\b|\bbabe\b|\bhoney\b", r"\bi'?ll (teach|guide|help) you\b"],
    "authority_claim": [r"\b(recruit\w*|hr|human resources|coordinator|manager|officer|agent)\b", r"\b(amazon|apple|microsoft|paypal|irs|fbi|police|bank|customs|fedex|usps|dhl)\b", r"\bon behalf of\b", r"\bofficial\b"],
    "urgency_pressure": [r"\b(tonight|today only|right now|immediately|asap|urgent\w*|hurry)\b", r"\bmove fast\b", r"\b(window|offer) (closes|ends|expires)\b", r"\bwithin \d+ (minutes|hours)\b", r"\b(suspended|frozen|locked)\b", r"\blimited (time|spots)\b"],
    "fake_proof_shown": [r"\bscreenshot\b", r"\bprofit\w*\b", r"\bproof\b", r"\b(look|see) (at )?(here|this|my)\b", r"\bearned \$", r"\breturns?\b.*\d+ ?%"],
    "small_payment": [r"\b(start small|small (amount|deposit))\b", r"\btop[- ]?up\b", r"\bdeposit\b", r"\b(registration|activation|training|processing) fee\b", r"\bsend \$?\d"],
    "large_payment": [r"\$\s?\d{1,3},\d{3}\b.*\b(deposit|send|transfer|invest)", r"\b(life savings|loan|remortgage|401k|retirement)\b", r"\binvest more\b"],
    "credentials_shared": [r"\b(password|passcode|verification code|otp|pin|ssn|social security|login)\b", r"\b(card|account|routing) number\b", r"\bphoto of (your )?id\b"],
    "remote_access_granted": [r"\b(anydesk|teamviewer|remote (access|desktop)|screen ?share|ultraviewer)\b"],
    "withdrawal_blocked": [r"\b(can'?t|cannot|unable to) withdraw\b", r"\bwithdraw\w*\b.*\b(tax|fee)\b", r"\bunlock (your )?(funds|account|earnings)\b", r"\baccount (is )?frozen\b"],
    "victim_suspicious": [r"\bis this a scam\b", r"\bsounds? (too good|fishy|suspicious)\b", r"\bnot sure (about|if)\b", r"\bare you real\b"],
    "third_party_intervened": [r"\bmy (bank|brother|sister|wife|husband|friend) (said|told|warned|blocked)\b", r"\bbank (blocked|stopped|flagged)\b", r"\bpolice\b.*\b(said|told)\b"],
    "victim_exits": [r"\b(blocked|reported) (him|her|them|the number)\b", r"\bstopped (replying|responding)\b"],
    "money_lost": [r"\b(lost|scammed out of) \$", r"\bnever (got|saw) (it|my money) back\b"],
}

SCAM_TYPE_CUES = {
    "pig_butchering": [(r"\bcrypto\w*\b", 2), (r"\b(usdt|bitcoin|btc|eth|trading|arbitrage|exchange|platform)\b", 1.5), (r"\bwrong (number|person)\b", 2.5), (r"\bmentor\b", 2), (r"\bprofit\w*\b", 1), (r"\b\d+ ?(to \d+ ?)?% (a|per) (week|day|month)\b", 2), (r"\bwine|golf|yoga\b", 0.5)],
    "fake_job": [(r"\b(job|role|position|part[- ]time|remote work|hiring|recruit\w*|resume|cv)\b", 2), (r"\b(task|tasks|rating|rate|like|liking|review\w*) (products?|apps?|videos?)\b", 2.5), (r"\b(commission|salary|daily pay|per day|daily)\b", 1.5), (r"\bno experience\b", 2), (r"\b(training|bonus|hr)\b", 1)],
    "impersonation": [(r"\b(irs|fbi|police|social security|customs|tax office|government)\b", 2.5), (r"\b(your bank|fraud department|tech support|microsoft|apple support)\b", 2.5), (r"\b(account (is )?(suspended|locked|compromised)|warrant|arrest)\b", 2), (r"\b(package|parcel|delivery) (held|failed|pending)\b", 2)],
    "marketplace": [(r"\b(facebook marketplace|craigslist|offerup|ebay|listing|item|for sale)\b", 2.5), (r"\b(buyer|seller|shipping|courier|pickup|overpa\w+)\b", 1.5), (r"\b(zelle|cashier'?s check|business account)\b", 1)],
    "romance": [(r"\b(love|darling|sweetheart|babe|honey|soulmate|my heart)\b", 2), (r"\b(dating|tinder|hinge|bumble|match)\b", 1.5), (r"\b(military|deployed|oil rig|hospital|flight ticket|visa)\b", 1.5)],
}

ADVICE = {
    "pig_butchering": "This matches a pig butchering script: a stranger who becomes a friend or mentor and pushes you onto a crypto platform. Do not deposit anything, stop replying and block the contact.",
    "fake_job": "This matches a fake job script: real employers never ask you to pay, top up an account or move to Telegram or WhatsApp to get work. Do not send money and stop contacting the recruiter.",
    "impersonation": "This matches an impersonation script: hang up or stop replying and contact the organisation yourself using the number on its official website. Never share codes, passwords or install remote access software.",
    "marketplace": "This matches a marketplace scam script: keep payment and messages inside the marketplace, never accept overpayments or off-platform payment links, and do not ship before funds clear.",
    "romance": "This matches a romance scam script: never send money or gifts to someone you have not met in person, however urgent their story. Talk to a friend or family member before doing anything.",
    "other": "This message shows warning signs of a scam. Do not send money or personal details, and verify the sender through an official channel before replying.",
}

DEFAULT_NEXT = {
    "initial_contact": "trust_building",
    "platform_switch": "trust_building",
    "trust_building": "fake_proof_shown",
    "authority_claim": "urgency_pressure",
    "urgency_pressure": "small_payment",
    "fake_proof_shown": "small_payment",
    "small_payment": "large_payment",
    "large_payment": "withdrawal_blocked",
    "credentials_shared": "money_lost",
    "remote_access_granted": "money_lost",
    "withdrawal_blocked": "large_payment",
    "victim_suspicious": "urgency_pressure",
    "third_party_intervened": "victim_exits",
    "victim_exits": "victim_exits",
    "money_lost": "victim_suspicious",
}

# Victim reactions, not script moves. They dominate story transitions because the stories are written by
# people who got suspicious, so they are never predicted as the scammer's next step.
REACTION_STEPS = {"victim_suspicious", "third_party_intervened", "victim_exits"}
SCRIPT_STEPS = [s for s in STEPS if s not in REACTION_STEPS]
# Steps the person does. Being asked to do them does not count as the step having happened.
VICTIM_ACTION_STEPS = {"small_payment", "large_payment", "credentials_shared", "remote_access_granted", "money_lost"}

_COMPILED_STEPS = {s: [re.compile(p, re.I) for p in STEP_CUES.get(s, [])] for s in STEPS}
_COMPILED_TYPES = {t: [(re.compile(p, re.I), w) for p, w in SCAM_TYPE_CUES.get(t, [])] for t in SCAM_TYPES}


def load_transitions(path: Path = STORIES_PATH) -> dict:
    """Most common next steps from stories: {(scam_type or None, step): Counter}."""
    table: dict = defaultdict(Counter)
    if not path.exists():
        return table
    try:
        stories = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return table
    for story in stories:
        steps = [e.get("step") for e in story.get("events", [])]
        for a, b in zip(steps, steps[1:]):
            if a in STEPS and b in STEPS and a != b:
                table[(story.get("scam_type"), a)][b] += 1
                table[(None, a)][b] += 1
    return table


def next_step(scam_type: str, seen: list[str], transitions: dict | None = None) -> str:
    if transitions is None:
        transitions = load_transitions()
    last = seen[-1] if seen else None
    if last is None:
        return "initial_contact"
    for key in ((scam_type, last), (None, last)):
        counts = transitions.get(key)
        ranked = [s for s, _ in (counts or Counter()).most_common() if s not in REACTION_STEPS]
        if ranked:
            unseen = [s for s in ranked if s not in seen]
            return (unseen or ranked)[0]
    return DEFAULT_NEXT.get(last, "small_payment")


def load_step_positions(path: Path = STORIES_PATH) -> dict:
    """Mean relative position (0 = first event, 1 = last) of each step across stories."""
    positions: dict = defaultdict(list)
    if path.exists():
        try:
            stories = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            stories = []
        for story in stories:
            steps = [e.get("step") for e in story.get("events", [])]
            for i, step in enumerate(steps):
                if step in STEPS:
                    positions[step].append(i / max(len(steps) - 1, 1))
    return {s: sum(p) / len(p) for s, p in positions.items()}


def rule_match(text: str, transitions: dict | None = None) -> dict:
    """Offline keyword and regex matcher. Returns a dict valid against the schema."""
    first_hit = {}
    for step, patterns in _COMPILED_STEPS.items():
        positions = [m.start() for p in patterns for m in [p.search(text)] if m]
        if positions:
            first_hit[step] = min(positions)
    steps_so_far = sorted(first_hit, key=lambda s: (first_hit[s], STEPS.index(s)))

    scores = {t: sum(w for p, w in pats if p.search(text)) for t, pats in _COMPILED_TYPES.items()}
    total = sum(scores.values())
    best = max(scores, key=scores.get) if total else "other"
    if total and scores[best] >= 2:
        share = scores[best] / total
        strength = min(scores[best] / 8, 1.0)
        confidence = round(min(0.95, 0.3 + 0.35 * share + 0.3 * strength), 2)
        scam_type = best
    else:
        scam_type, confidence = "other", round(min(0.3, 0.05 * len(steps_so_far) + 0.05), 2)

    return validate({
        "scam_type": scam_type,
        "confidence": confidence,
        "steps_so_far": steps_so_far,
        "likely_next_step": next_step(scam_type, steps_so_far, transitions),
        "advice": ADVICE.get(scam_type, ADVICE["other"]),
    })


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _cache_path(text: str) -> Path:
    return CACHE_DIR / f"{_text_hash(text)}.json"


def _read_cache(text: str) -> dict | None:
    path = _cache_path(text)
    if not path.exists():
        return None
    try:
        return validate(json.loads(path.read_text()))
    except (json.JSONDecodeError, jsonschema.ValidationError):
        return None


def _write_cache(text: str, result: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(text).write_text(json.dumps(result, indent=2))


def _normalise(text: str) -> str:
    return " ".join(text.split())


def demo_response(text: str) -> dict | None:
    """Return the stored response if text is one of the demo messages."""
    messages = json.loads(DEMO_MESSAGES_PATH.read_text())
    responses = json.loads(DEMO_RESPONSES_PATH.read_text())
    key = _normalise(text)
    for msg in messages:
        if _normalise(msg["text"]) == key:
            return validate(responses[msg["id"]])
    return None


def _env_key(name: str) -> str | None:
    """Read a key from the environment, then from a repo-root .env. Never logged."""
    key = os.environ.get(name)
    if key:
        return key
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if line.startswith(f"{name}="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return None


def _load_api_key() -> str | None:
    return _env_key("ANTHROPIC_API_KEY")


def _load_typesafe_key() -> str | None:
    return _env_key("TYPESAFE_API_KEY")


def _make_client():
    key = _load_api_key()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in the environment or .env")
    import anthropic

    return anthropic.Anthropic(api_key=key)


def _call_model(text: str, client) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": f"Text to analyse:\n<text>\n{text}\n</text>"}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            return validate(dict(block.input))
    raise RuntimeError("model did not return a tool_use block")

# Jev (TypeSafe System One): typed judgments, assembled into a match by code

JEV_STATE_NOTE = "A person received this message or conversation and pasted it to check whether it is a scam."
LOW_RISK_ADVICE = (
    "This does not clearly match a known scam script. Still, do not send money, codes or personal details "
    "to anyone you have not verified through an official channel."
)


def jev_questions() -> dict:
    """Choices for the scam type and the next step, a Noul for "is this a scam at all" and a Noul per step."""
    from typesafe_sdk import Choice, Noul

    questions = {
        "scam_type": Choice(
            instructions="Which scam script does `pasted_text` match best? Choose other if it matches none of them or looks harmless.",
            criteria={t: SCAM_TYPE_GUIDE[t] for t in SCAM_TYPES},
        ),
        "is_scam": Noul(instructions="Does `pasted_text` show signs of a scam attempt against the person who received it?"),
    }
    for step in STEPS:
        if step in VICTIM_ACTION_STEPS:
            instructions = (
                f"Has the person who received `pasted_text` already done this: {STEP_GUIDE[step]}? "
                "Being asked or pressured to do it does not count."
            )
        elif step in REACTION_STEPS:
            instructions = (
                f"Does `pasted_text` show this really happening: {STEP_GUIDE[step]}? "
                "The scammer pretending to be a bank, police or someone else stepping in does not count."
            )
        else:
            instructions = (
                f"Does `pasted_text` show this scam step: {STEP_GUIDE[step]}? "
                "Count it if the scammer does it or asks the person to go along with it."
            )
        questions[f"step:{step}"] = Noul(instructions=instructions)
    questions["next_step"] = Choice(
        instructions=(
            "If the person keeps engaging with the sender of `pasted_text`, what is the scammer most likely to get them "
            "to do or experience next? Pick a step that has not happened yet."
        ),
        criteria={s: STEP_GUIDE[s] for s in SCRIPT_STEPS},
    )
    return questions


def _make_jev_client():
    key = _load_typesafe_key()
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY not set in the environment or .env")
    from typesafe_sdk import RetryPolicy, TypeSafeClient

    return TypeSafeClient(api_key=key, model=JEV_MODEL, timeout=JEV_TIMEOUT, retry=RetryPolicy(max_retries=1))


def jev_result(response, transitions: dict | None = None, positions: dict | None = None) -> dict:
    """Turn a System One response into a schema-valid match.

    Seen steps are ordered by where they usually fall in the stories. The next step is the script step Jev
    rates most likely among those not seen yet, otherwise the most common story transition.
    """
    if positions is None:
        positions = load_step_positions()
    type_answer = response.choices["scam_type"]
    p_scam = response.nouls["is_scam"].noul
    seen = [s for s in STEPS if response.nouls[f"step:{s}"].noul > NOUL_THRESHOLD]
    seen.sort(key=lambda s: (positions.get(s, STEPS.index(s) / len(STEPS)), STEPS.index(s)))

    if p_scam > NOUL_THRESHOLD:
        scam_type = type_answer.choice
        confidence = type_answer.probabilities.get(scam_type, type_answer.confidence) * p_scam
        advice = ADVICE.get(scam_type, ADVICE["other"])
    else:
        scam_type, confidence, advice = "other", p_scam, LOW_RISK_ADVICE

    # Questions in one request cannot see each other, so Jev's top pick may be a step it also saw. Take the most
    # likely step not seen yet, and fall back to the story transitions if Jev gives none of them any weight.
    probabilities = response.choices["next_step"].probabilities
    unseen = sorted(((p, s) for s, p in probabilities.items() if s in SCRIPT_STEPS and s not in seen), reverse=True)
    if unseen and unseen[0][0] >= NEXT_STEP_MIN_PROBABILITY:
        jev_next = unseen[0][1]
    else:
        jev_next = next_step(scam_type, seen, transitions)

    return validate({
        "scam_type": scam_type,
        "confidence": round(min(max(confidence, 0.0), 1.0), 2),
        "steps_so_far": seen,
        "likely_next_step": jev_next,
        "advice": advice,
    })


def _call_jev(text: str, client) -> dict:
    response = client.system_one(
        state={"context": JEV_STATE_NOTE, "pasted_text": text},
        questions=jev_questions(),
        model=JEV_MODEL,
    )
    return jev_result(response)


def match_with_source(text: str, client=None, use_cache: bool = True, jev_client=None) -> tuple[dict, str]:
    """Like match(), but also says which path produced the result.

    Source is one of "cache", "jev", "api", "demo" or "rules". Order:
    1. disk cache of earlier valid model answers,
    2. Jev, only when a jev_client is passed or TYPESAFE_API_KEY is set (environment or .env),
    3. Sonnet, only when a client is passed or ANTHROPIC_API_KEY is set (environment or .env),
    4. the stored response if the text is one of the demo messages,
    5. the offline rule-based matcher.
    A failed call or an answer that does not match the schema falls through to the next step.
    Only valid model answers are cached.
    """
    if not text or not text.strip():
        raise ValueError("text is empty")
    if use_cache:
        cached = _read_cache(text)
        if cached is not None:
            return cached, "cache"
    if jev_client is not None or _load_typesafe_key():
        try:
            if jev_client is not None:
                result = _call_jev(text, jev_client)
            else:
                with _make_jev_client() as owned:
                    result = _call_jev(text, owned)
        except jsonschema.ValidationError as exc:
            log.warning("matcher: Jev result failed schema validation, using fallback: %s", exc.message)
        except Exception as exc:  # network, auth, rate limit, missing answer
            log.warning("matcher: Jev call failed, using fallback: %s", type(exc).__name__)
        else:
            if use_cache:
                _write_cache(text, result)
            return result, "jev"
    if client is not None or _load_api_key():
        try:
            result = _call_model(text, client or _make_client())
        except jsonschema.ValidationError as exc:
            log.warning("matcher: model output failed schema validation, using fallback: %s", exc.message)
        except Exception as exc:  # network, auth, rate limit, missing tool_use block
            log.warning("matcher: model call failed, using fallback: %s", type(exc).__name__)
        else:
            if use_cache:
                _write_cache(text, result)
            return result, "api"
    demo = demo_response(text)
    if demo is not None:
        return demo, "demo"
    return rule_match(text), "rules"


def match(text: str, client=None, use_cache: bool = True, jev_client=None) -> dict:
    """Match text against scam scripts. Returns a dict valid against contract/matcher.schema.json."""
    return match_with_source(text, client=client, use_cache=use_cache, jev_client=jev_client)[0]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = " ".join(args) if args else sys.stdin.read()
    result, source = match_with_source(text)
    print(f"[matcher source: {source}]", file=sys.stderr)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
