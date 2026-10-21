"""Live matcher: classify a pasted message or conversation against the scam script vocabulary.

Usage: python -m pipeline.matcher "text"
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_DIR = Path(__file__).resolve().parent
CONTRACT_DIR = ROOT / "contract"
CACHE_DIR = PIPELINE_DIR / ".cache" / "matcher"
DEMO_MESSAGES_PATH = PIPELINE_DIR / "demo_messages.json"
DEMO_RESPONSES_PATH = PIPELINE_DIR / "demo_responses.json"

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
    """Match text against scam scripts. Returns a dict valid against contract/matcher.schema.json."""
    if not text or not text.strip():
        raise ValueError("text is empty")
    if use_cache:
        cached = _read_cache(text)
        if cached is not None:
            return cached
    try:
        if client is None:
            client = _make_client()
        result = _call_model(text, client)
    except Exception:
        fallback = demo_response(text)
        if fallback is None:
            raise
        return fallback
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
