"""Extraction prompt, tool schema and vocabulary, shared by the extractor and the matcher."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

VOCAB_PATH = Path(__file__).resolve().parent.parent / "contract" / "vocab.json"
VOCAB: dict[str, list[str]] = json.loads(VOCAB_PATH.read_text())

STEPS: list[str] = VOCAB["steps"]
SCAM_TYPES: list[str] = VOCAB["scam_types"]
ACTORS: list[str] = VOCAB["actors"]
OUTCOMES: list[str] = VOCAB["outcomes"]
SOURCES: list[str] = VOCAB["sources"]

EXTRACT_MODEL = "claude-haiku-5-5"
TOOL_NAME = "record_story"

STEP_GUIDE = {
    "initial_contact": "first contact between scammer and victim",
    "platform_switch": "conversation moved to another app or channel",
    "trust_building": "rapport, friendship, romance or credibility being built",
    "authority_claim": "scammer claims to be a bank, government, company, recruiter or other authority",
    "urgency_pressure": "deadlines, threats or pressure to act fast",
    "fake_proof_shown": "fake profits, documents, screenshots or credentials shown",
    "small_payment": "victim makes a small or first payment",
    "large_payment": "victim makes a large or follow-up payment",
    "credentials_shared": "victim shares passwords, codes, card or ID details",
    "remote_access_granted": "victim installs remote access software or gives device access",
    "withdrawal_blocked": "victim cannot withdraw, told to pay fees or taxes first",
    "victim_suspicious": "victim starts doubting or researching",
    "third_party_intervened": "bank, family, friend or police steps in",
    "victim_exits": "victim stops engaging or gets out",
    "money_lost": "victim realises the money is gone",
}

SCAM_TYPE_GUIDE = {
    "pig_butchering": "long-con crypto or trading investment scam, often after a wrong-number text or dating contact",
    "fake_job": "fake job, task or recruiter scam",
    "impersonation": "pretending to be a bank, government agency, tech support or a known company",
    "marketplace": "buying or selling goods online (fake buyer, fake seller, overpayment)",
    "romance": "romantic relationship used to extract money, without an investment angle",
    "other": "anything else",
}

SYSTEM_PROMPT = f"""You turn first-person scam victim stories into structured event sequences.

Read the post and call the `{TOOL_NAME}` tool exactly once.

Rules:
- events: the scam as it happened, in chronological order, using only these steps:
{chr(10).join(f"  - {s}: {d}" for s, d in STEP_GUIDE.items())}
- actor is who performed the step: scammer, victim or third_party.
- detail: a short phrase (under 15 words). No names, usernames, emails, phone numbers, URLs or wallet addresses.
- scam_type, one of:
{chr(10).join(f"  - {s}: {d}" for s, d in SCAM_TYPE_GUIDE.items())}
- outcome: lost_money if the victim lost any money at all (even if they later got out); escaped if they got out with no money lost; unknown if the post doesn't say.
- amount_lost_usd: total loss converted to US dollars, or null if not stated or nothing was lost.
- snippet: 1 to 2 sentence anonymised summary in the third person. No names, usernames, emails, phone numbers.
- Only include events the post supports. Do not invent steps."""

STORY_TOOL = {
    "name": TOOL_NAME,
    "description": "Record the structured scam story extracted from the post.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["scam_type", "events", "outcome", "amount_lost_usd", "snippet"],
        "properties": {
            "scam_type": {"type": "string", "enum": SCAM_TYPES},
            "events": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["step", "actor", "detail"],
                    "properties": {
                        "step": {"type": "string", "enum": STEPS},
                        "actor": {"type": "string", "enum": ACTORS},
                        "detail": {"type": "string"},
                    },
                },
            },
            "outcome": {"type": "string", "enum": OUTCOMES},
            "amount_lost_usd": {"type": ["number", "null"]},
            "snippet": {"type": "string"},
        },
    },
}


def user_message(text: str) -> str:
    return f"<post>\n{text}\n</post>"


def prompt_hash() -> str:
    """Short hash of everything that shapes the model's answer, used as a cache key."""
    blob = json.dumps([EXTRACT_MODEL, SYSTEM_PROMPT, STORY_TOOL], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]
