"""Prompts and contract plumbing shared by extraction and the matcher.

Everything the model needs to know about the shared vocabulary lives here, so
``pipeline.extract`` (post -> Story) and ``pipeline.matcher`` (pasted text ->
match result) describe the steps the same way and validate against the same
contract files.

The enums are always read from ``contract/vocab.json`` and the schemas from
``contract/*.schema.json``; nothing here redefines them.
"""

from __future__ import annotations

import copy
import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

CONTRACT_DIR = Path(__file__).resolve().parent.parent / "contract"

EXTRACT_MODEL = "claude-haiku-5-5"


# --------------------------------------------------------------------------
# Contract files
# --------------------------------------------------------------------------


@cache
def load_vocab() -> dict[str, list[str]]:
    return json.loads((CONTRACT_DIR / "vocab.json").read_text())


@cache
def load_schema(name: str) -> dict[str, Any]:
    """Load ``contract/<name>.schema.json``, e.g. ``load_schema("story")``."""
    return json.loads((CONTRACT_DIR / f"{name}.schema.json").read_text())


@cache
def story_validator() -> Draft202012Validator:
    """Validator for a single story (``$defs/story`` of the stories file schema)."""
    schema = load_schema("story")
    return Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/story"})


@cache
def stories_file_validator() -> Draft202012Validator:
    """Validator for the whole ``data/stories.json`` array."""
    return Draft202012Validator(load_schema("story"))


# --------------------------------------------------------------------------
# Schemas for structured output
# --------------------------------------------------------------------------

# Structured outputs reject these keywords; jsonschema re-checks them locally.
UNSUPPORTED_KEYWORDS = frozenset(
    {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
     "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems"}
)
DROPPED_KEYWORDS = frozenset({"$schema", "$id", "title"})


def to_api_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a contract schema acceptable to ``output_config.format``.

    Inlines ``$ref``s, drops constraints the API doesn't support, and turns
    ``"type": [X, "null"]`` into an ``anyOf``. The result is looser than the
    contract, so always validate the model's output with the real schema.
    """
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(copy.deepcopy(defs[node["$ref"].removeprefix("#/$defs/")]))
        out = {
            key: walk(value)
            for key, value in node.items()
            if key not in UNSUPPORTED_KEYWORDS | DROPPED_KEYWORDS and key != "$defs"
        }
        if isinstance(out.get("type"), list):
            types = out.pop("type")
            out["anyOf"] = [{"type": t} for t in types]
        return out

    return walk(schema)


@cache
def extraction_output_schema() -> dict[str, Any]:
    """What the model returns for one post: a Story without ``id`` and ``source``.

    Those two come from the post itself, so the model never has to copy them.
    """
    story = copy.deepcopy(load_schema("story")["$defs"]["story"])
    for key in ("id", "source"):
        story["properties"].pop(key)
        story["required"].remove(key)
    story["$defs"] = load_schema("story")["$defs"]
    return to_api_schema(story)


# --------------------------------------------------------------------------
# Vocabulary guide
# --------------------------------------------------------------------------

STEP_GUIDE: dict[str, str] = {
    "initial_contact": "First contact: a text, DM, dating match, job message, call, ad or listing reply.",
    "platform_switch": "The conversation moves to another app or channel (WhatsApp, Telegram, Signal, email, a phone call).",
    "trust_building": "Rapport over time: friendly chat, romance, shared photos, talk of a future together, small favours.",
    "authority_claim": "The scammer claims to be or speak for an official body or company: a bank, government agency, police, tech support, a recruiter or employer.",
    "urgency_pressure": "Deadlines, threats, guilt or pressure to act now (account will be frozen, arrest, offer expires, 'pay or lose everything').",
    "fake_proof_shown": "Fabricated evidence: profit screenshots, a fake trading or task platform, fake documents, IDs, invoices or payment confirmations.",
    "small_payment": "The victim's first or test payment, fee, deposit or purchase (usually small).",
    "large_payment": "The victim sends a much bigger amount or keeps adding money (savings, loans, repeated deposits).",
    "credentials_shared": "The victim hands over passwords, one-time codes, bank logins, ID documents or card details.",
    "remote_access_granted": "The victim installs remote-access software or lets the scammer control a device or account.",
    "withdrawal_blocked": "The victim can't get money out: 'taxes', 'fees', 'frozen account', or a demand to pay more to release funds.",
    "victim_suspicious": "The victim starts doubting: research, a reverse image search, noticing red flags, asking questions.",
    "third_party_intervened": "Someone else steps in: a bank, family member, friend, platform, police, or Reddit commenters warning them.",
    "victim_exits": "The victim stops engaging, blocks the scammer, or refuses to pay more.",
    "money_lost": "The victim realises the money is gone (contact goes silent, account vanishes, funds not recovered).",
}

SCAM_TYPE_GUIDE: dict[str, str] = {
    "pig_butchering": "A long relationship (often a wrong-number text or dating match) that turns into a fake investment or crypto trading platform.",
    "fake_job": "Fake job, task or recruitment offers: paid 'tasks', check-cashing, reshipping, upfront fees for training or equipment.",
    "impersonation": "Someone pretending to be a bank, government agency, police, tech support or a well-known company.",
    "marketplace": "Buying or selling goods or rentals online: fake buyers, fake sellers, overpayment, fake shipping or escrow.",
    "romance": "A romantic relationship used to ask for money (gifts, emergencies, travel) without a fake investment platform.",
    "other": "Anything that doesn't fit the types above.",
}


def _check_guides() -> None:
    vocab = load_vocab()
    if list(STEP_GUIDE) != vocab["steps"]:
        raise RuntimeError("STEP_GUIDE is out of sync with contract/vocab.json")
    if list(SCAM_TYPE_GUIDE) != vocab["scam_types"]:
        raise RuntimeError("SCAM_TYPE_GUIDE is out of sync with contract/vocab.json")


_check_guides()


def vocabulary_text() -> str:
    """The step and scam-type definitions as a bullet list for a system prompt."""
    steps = "\n".join(f"- {name}: {text}" for name, text in STEP_GUIDE.items())
    types = "\n".join(f"- {name}: {text}" for name, text in SCAM_TYPE_GUIDE.items())
    return f"Steps:\n{steps}\n\nScam types:\n{types}"


# --------------------------------------------------------------------------
# Extraction prompt
# --------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = f"""\
You turn first-person scam reports (Reddit posts and consumer complaints) into
structured event sequences. The output feeds a graph of how scams unfold, so
the order and choice of steps matter more than detail.

{vocabulary_text()}

How to build the story:
- events: what happened, in chronological order, one event per distinct step.
  Use only the steps above. Repeat a step only if it really happened again
  later (e.g. a second round of urgency_pressure after a blocked withdrawal).
  Include only steps the text supports; don't invent missing ones.
- actor: who did it. scammer for scammer actions, victim for the victim's own
  actions (payments, sharing credentials, getting suspicious, exiting),
  third_party for banks, family, police, platforms or commenters.
- detail: a short phrase (under 15 words) saying what happened at that step.
- outcome: lost_money if the victim lost any money at all, even if they later
  got out; escaped if they got out without losing money; unknown if the text
  doesn't say. An escape still appears as a victim_exits event.
- amount_lost_usd: the total the victim lost, in US dollars, as a number.
  Convert other currencies roughly. null if no loss or the amount isn't stated.
- scam_type: the single best fit from the list above.
- snippet: a 1 to 2 sentence summary of the story for a UI.

Anonymise everything you write. Never include names, usernames, emails, phone
numbers, wallet addresses, account numbers or exact addresses; say "the
victim", "the scammer", "a recruiter" and so on. The input already has some
PII replaced with placeholders like [name], [user], [email], [number] or
[wallet]; don't copy the placeholders into your output either. Keep platform,
app and company names (WhatsApp, Coinbase, Zelle) and dollar amounts.

If the text is a post about a scam the author spotted but never engaged with,
still extract what happened (often initial_contact, victim_suspicious,
victim_exits with outcome escaped).
"""


def extraction_user_message(text: str) -> str:
    return f"<post>\n{text}\n</post>"


def prompt_hash(*parts: Any) -> str:
    """Stable hash of everything that shapes a model call, for cache keys."""
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()
