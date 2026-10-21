"""Live matcher: classify a pasted message or conversation against the scam script vocabulary.

Usage: python -m pipeline.matcher "text"
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_DIR = Path(__file__).resolve().parent
CONTRACT_DIR = ROOT / "contract"
CACHE_DIR = PIPELINE_DIR / ".cache" / "matcher"
DEMO_MESSAGES_PATH = PIPELINE_DIR / "demo_messages.json"
DEMO_RESPONSES_PATH = PIPELINE_DIR / "demo_responses.json"
STORIES_PATH = ROOT / "data" / "stories.json"

MODEL = "claude-sonnet-5-5"
TOOL_NAME = "report_match"

VOCAB = json.loads((CONTRACT_DIR / "vocab.json").read_text())
SCHEMA = json.loads((CONTRACT_DIR / "matcher.schema.json").read_text())
STEPS: list[str] = VOCAB["steps"]
SCAM_TYPES: list[str] = VOCAB["scam_types"]

STEP_DESCRIPTIONS = {
    "initial_contact": "The scammer first reaches the victim (cold message, wrong-number text, dating match, job ad, recruiter outreach, marketplace reply).",
    "platform_switch": "The scammer moves the conversation to another app or channel (WhatsApp, Telegram, Signal, a private site).",
    "trust_building": "Rapport or friendship building: small talk, compliments, personal stories, mentoring offers, romance.",
    "authority_claim": "The scammer claims to be an official, company, bank, recruiter, expert or well-known brand.",
    "urgency_pressure": "Time pressure or threats: act now, window closing, account frozen, deadline.",
    "fake_proof_shown": "Fabricated evidence: profit screenshots, fake dashboards, fake IDs, documents, payment confirmations.",
    "small_payment": "The victim sends or is asked to send a first small amount (test deposit, fee, top-up).",
    "large_payment": "The victim sends a large amount, often after the small payment appeared to work.",
    "credentials_shared": "The victim shares logins, codes, card numbers, ID documents or other sensitive details.",
    "remote_access_granted": "The victim installs remote access software or lets the scammer control a device.",
    "withdrawal_blocked": "The victim cannot withdraw or recover money; asked for taxes, fees or more deposits to unlock it.",
    "victim_suspicious": "The victim starts doubting, questions the story or researches the scammer.",
    "third_party_intervened": "A bank, friend, family member, police or platform steps in.",
    "victim_exits": "The victim stops engaging and gets out.",
    "money_lost": "The victim has lost money that is not recovered.",
}

SCAM_TYPE_DESCRIPTIONS = {
    "pig_butchering": "Long con that builds a relationship (often after a wrong-number text or dating match) then pushes fake crypto or trading investments.",
    "fake_job": "Fake recruiter or task job (rating products, data entry, reshipping) that ends with the victim paying to work or unlock earnings.",
    "impersonation": "Scammer poses as a bank, government agency, tech support, delivery company or known person.",
    "marketplace": "Buying or selling scam on a marketplace: fake buyer, overpayment, fake shipping, nonexistent item.",
    "romance": "Romantic relationship used to ask for money for emergencies, travel or gifts, without an investment angle.",
    "other": "Anything that does not fit the types above.",
}


def _build_system_prompt() -> str:
    steps = "\n".join(f"- {s}: {STEP_DESCRIPTIONS.get(s, s)}" for s in STEPS)
    types = "\n".join(f"- {t}: {SCAM_TYPE_DESCRIPTIONS.get(t, t)}" for t in SCAM_TYPES)
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
            "advice": {"type": "string"},
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
        if counts:
            ranked = [s for s, _ in counts.most_common()]
            unseen = [s for s in ranked if s not in seen]
            return (unseen or ranked)[0]
    return DEFAULT_NEXT.get(last, "small_payment")


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


def _load_api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if line.startswith("ANTHROPIC_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return None


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


def match(text: str, client=None, use_cache: bool = True) -> dict:
    """Match text against scam scripts. Returns a dict valid against contract/matcher.schema.json.

    Order: demo responses, disk cache, Sonnet (only when a client or API key is available),
    then the offline rule-based matcher.
    """
    if not text or not text.strip():
        raise ValueError("text is empty")
    demo = demo_response(text)
    if demo is not None:
        return demo
    if use_cache:
        cached = _read_cache(text)
        if cached is not None:
            return cached
    result = None
    if client is not None or _load_api_key():
        try:
            result = _call_model(text, client or _make_client())
        except jsonschema.ValidationError:
            raise
        except Exception:
            result = None
    if result is None:
        return rule_match(text)
    if use_cache:
        _write_cache(text, result)
    return result


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = " ".join(args) if args else sys.stdin.read()
    print(json.dumps(match(text), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
