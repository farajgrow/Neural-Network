"""Clean raw scam posts into story text ready for extraction.

Reads ``data/raw/posts.jsonl`` (written by ``pipeline.scrape``) and writes
``data/clean/posts.jsonl``. See ``pipeline/README.md`` for both formats.

Each post goes through, in order:

1. Dedupe by ``id``.
2. Drop removed, deleted or empty bodies.
3. Drop mod/meta posts.
4. Strip markdown and links, then remove PII (see ``redact_pii``).
5. Drop posts that are mostly a link or a screenshot reference.
6. Drop posts shorter than ``MIN_CHARS`` after cleaning.
7. Dedupe near-identical bodies (crossposts and reposts).

Every filter is a small pure function so it can be tested on its own.

Usage::

    python -m pipeline.clean [--input data/raw/posts.jsonl] [--output data/clean/posts.jsonl]
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

DEFAULT_INPUT = Path("data/raw/posts.jsonl")
DEFAULT_OUTPUT = Path("data/clean/posts.jsonl")

MIN_CHARS = 300
LINK_ONLY_MAX_WORDS = 50
QUESTION_ONLY_MAX_WORDS = 80
LINK_SHARE_THRESHOLD = 0.5

# Order matters: this is also the order of the summary printed by main().
DROP_REASONS = (
    "duplicate_id",
    "removed_or_empty",
    "meta_post",
    "link_or_screenshot_only",
    "too_short",
    "duplicate_text",
)


# --------------------------------------------------------------------------
# Markdown and link stripping
# --------------------------------------------------------------------------

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]{}]*[^\s<>()\[\]{}.,;:!?'\"]")
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
# Line-anchored patterns use [ \t] rather than \s so they never eat a blank line.
CODE_FENCE_RE = re.compile(r"^[ \t]*(?:```|~~~).*$", re.M)
INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
HORIZONTAL_RULE_RE = re.compile(r"^[ \t]*(?:[-*_][ \t]*){3,}$", re.M)
HEADING_RE = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.M)
SPOILER_RE = re.compile(r">!(.+?)!<")
BLOCKQUOTE_RE = re.compile(r"^[ \t]*(?:>[ \t]?)+", re.M)
LIST_MARKER_RE = re.compile(r"^[ \t]*(?:[-*+\u2022]|\d{1,3}[.)])[ \t]+", re.M)
STAR_EMPHASIS_RE = re.compile(r"(\*{1,3})(?=\S)(.+?)(?<=\S)\1")
UNDERSCORE_EMPHASIS_RE = re.compile(r"(?<!\w)(_{1,3})(?=\S)(.+?)(?<=\S)\1(?!\w)")
STRIKETHROUGH_RE = re.compile(r"~~(.+?)~~")
SUPERSCRIPT_RE = re.compile(r"\^\(([^)]*)\)|\^(?=\S)")
ESCAPED_CHAR_RE = re.compile(r"\\([^\w\s])")
EMPTY_BRACKETS_RE = re.compile(r"\(\s*\)|\[\s*\]")
INLINE_SPACE_RE = re.compile(r"[ \t\u00a0]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")


def unescape_html(text: str) -> str:
    """Decode HTML entities, including Reddit's double-escaped ``&amp;amp;``."""
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return text


def strip_links(text: str) -> str:
    """Turn ``[text](url)`` into ``text``, drop images and remove bare URLs."""
    text = MD_IMAGE_RE.sub("", text)
    text = MD_LINK_RE.sub(r"\1", text)
    text = URL_RE.sub("", text)
    return EMPTY_BRACKETS_RE.sub("", text)


def normalise_whitespace(text: str) -> str:
    """Collapse runs of spaces, trim each line and keep at most one blank line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = INLINE_SPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return BLANK_LINES_RE.sub("\n\n", text).strip()


def strip_markdown(text: str) -> str:
    """Reduce Reddit markdown to plain text, keeping the words and dropping the syntax."""
    text = unescape_html(text)
    text = ZERO_WIDTH_RE.sub("", text)
    text = CODE_FENCE_RE.sub("", text)
    text = INLINE_CODE_RE.sub(r"\1", text)
    text = strip_links(text)
    text = HORIZONTAL_RULE_RE.sub("", text)
    text = HEADING_RE.sub("", text)
    text = SPOILER_RE.sub(r"\1", text)
    text = BLOCKQUOTE_RE.sub("", text)
    text = LIST_MARKER_RE.sub("", text)
    text = STAR_EMPHASIS_RE.sub(r"\2", text)
    text = UNDERSCORE_EMPHASIS_RE.sub(r"\2", text)
    text = STRIKETHROUGH_RE.sub(r"\1", text)
    text = SUPERSCRIPT_RE.sub(lambda m: m.group(1) or "", text)
    text = ESCAPED_CHAR_RE.sub(r"\1", text)
    return normalise_whitespace(text)


# --------------------------------------------------------------------------
# PII removal
# --------------------------------------------------------------------------

# CFPB narratives arrive pre-redacted: amounts in braces, everything else as X.
CFPB_AMOUNT_RE = re.compile(r"\{\s*(\$?)\s*([\d,]+(?:\.\d+)?)\s*\}")
CFPB_DATE_RE = re.compile(r"\bXX/XX/(XXXX|\d{2,4})\b")
CFPB_MASKED_NUMBER_RE = re.compile(r"\bX{2,}-?\d{2,}\b")
CFPB_MASK_RE = re.compile(r"\bX{2,}(?:[\s/-]+X{2,})*\b")

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")

BASE58 = "1-9A-HJ-NP-Za-km-z"
WALLET_RE = re.compile(
    rf"""
    \b0x[a-fA-F0-9]{{40}}\b                          # Ethereum and EVM chains
    | \b(?i:bc1)[a-zA-HJ-NP-Z0-9]{{11,71}}\b         # Bitcoin bech32
    | \b[13](?=[{BASE58}]*[A-Za-z])[{BASE58}]{{25,34}}\b  # Bitcoin legacy
    | \bT[{BASE58}]{{33}}\b                          # Tron
    """,
    re.X,
)

SSN_RE = re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b")
CARD_RE = re.compile(r"\b(?:\d{4}[ -]){3}\d{1,7}\b|\b\d{4}[ -]\d{6}[ -]\d{5}\b")
PHONE_RE = re.compile(
    r"""
    (?<![\w$.,/+-])
    (?:
        \+\d{1,3}[\s.-]?(?:\(?\d{1,4}\)?[\s.-]?){1,4}\d{2,4}   # international, +44 20 7946 0958
      | (?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}          # US, (555) 123-4567 or 555.123.4567
      | \d{10}                                              # US, 5551234567
    )
    (?![\w-])
    """,
    re.X,
)
LONG_NUMBER_RE = re.compile(r"(?<![\w$.,])\d{8,}(?![\w])")
MIN_INTERNATIONAL_PHONE_DIGITS = 8

REDDIT_USER_RE = re.compile(r"(?<![\w/])/?u/[\w-]{3,20}\b")
HANDLE_RE = re.compile(r"(?<![\w@.])@[A-Za-z_](?:[\w.]*\w)?")

# Words that look like names to a regex but that extraction needs to keep.
PROTECTED_WORDS = {
    word.lower()
    for word in """
    WhatsApp Telegram Signal Zelle Venmo PayPal CashApp Cash Chime Coinbase Binance
    Kraken Crypto Bitcoin Ethereum Tether USDT MetaMask Trust Wallet Western Union
    MoneyGram Instagram Facebook Messenger Marketplace Tinder Hinge Bumble Grindr
    LinkedIn Discord Snapchat WeChat Line Kik Skype Teams Zoom Google Apple Amazon
    Microsoft Steam Etsy eBay Craigslist OfferUp Upwork Indeed Fiverr Reddit Twitter
    TikTok YouTube IRS FBI FTC SSA Bank Wells Fargo Citi Capital One
    """.split()
}
SIGNOFF_STOP_WORDS = frozenset({
    "everyone", "everybody", "all", "guys", "folks", "you", "again", "anyone", "team",
    "thanks", "thank", "cheers", "regards", "help", "please", "edit", "update",
})

NAME = r"[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?"
FULL_NAME = rf"{NAME}(?:\s+{NAME})?"
INTRO_NAME_RE = re.compile(
    rf"(?P<lead>\b(?i:(?:my|his|her|their) name (?:is|was)|named|goes by|went by)\s+)"
    rf"(?P<name>{FULL_NAME})\b"
)
TITLE_NAME_RE = re.compile(rf"\b(?P<lead>(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s+)(?P<name>{FULL_NAME})\b")
SIGNOFF_RE = re.compile(
    rf"(?P<lead>(?:\b(?i:thanks(?: in advance)?|thank you|regards|best(?: regards| wishes)?"
    rf"|kind regards|cheers|sincerely|respectfully)[,.!]?\s+|,[ \t]*\n|\n[ \t]*\n|^[-\u2013\u2014~]+\s*))"
    rf"(?P<name>{NAME}(?:\s+[A-Z](?:[a-z]+|\.))?)[.!]?\s*\Z",
    re.M,
)


def normalise_cfpb_redactions(text: str) -> str:
    """Make CFPB's built-in redactions readable.

    ``{$1200.00}`` becomes ``$1200.00`` so amounts survive, ``XX/XX/XXXX``
    becomes ``[date]`` (keeping the year when CFPB left it), masked account
    numbers like ``XXXX1234`` become ``[number]`` and any other run of ``XXXX``
    becomes ``[redacted]``.
    """
    text = CFPB_AMOUNT_RE.sub(r"\1\2", text)
    text = CFPB_DATE_RE.sub(
        lambda m: "[date]" if m.group(1) == "XXXX" else f"[date] {m.group(1)}", text
    )
    text = CFPB_MASKED_NUMBER_RE.sub("[number]", text)
    return CFPB_MASK_RE.sub("[redacted]", text)


def _redact_phone(match: re.Match[str]) -> str:
    phone = match.group(0)
    digits = sum(ch.isdigit() for ch in phone)
    if phone.startswith("+") and digits < MIN_INTERNATIONAL_PHONE_DIGITS:
        return phone  # Too short to be a phone number, e.g. "+50000" in a fake balance.
    return "[phone]"


def redact_contacts(text: str) -> str:
    """Replace emails, wallets, numbers, phones and usernames with placeholders.

    Wallets and numbers run before phones so a card number is never half-eaten
    as a phone number. Dollar amounts, years and dates are left alone: every
    number pattern refuses to start right after ``$``, a digit or a separator.
    """
    text = EMAIL_RE.sub("[email]", text)
    text = WALLET_RE.sub("[wallet]", text)
    text = SSN_RE.sub("[number]", text)
    text = CARD_RE.sub("[number]", text)
    text = PHONE_RE.sub(_redact_phone, text)
    text = LONG_NUMBER_RE.sub("[number]", text)
    text = REDDIT_USER_RE.sub("[user]", text)
    return HANDLE_RE.sub("[user]", text)


def _redact_name(match: re.Match[str], stop_words: frozenset[str] = frozenset()) -> str:
    words = match.group("name").split()
    first = words[0].lower()
    if first in PROTECTED_WORDS or first in stop_words:
        return match.group(0)
    # Keep a trailing word that is really a platform, e.g. "named Jessica WhatsApp".
    tail = f" {words[1]}" if len(words) > 1 and words[1].lower() in PROTECTED_WORDS else ""
    return f"{match.group('lead')}[name]{tail}"


def redact_names(text: str) -> str:
    """Replace personal names that appear in a few unambiguous patterns.

    Handled: "my/his/her name is X", "named X", "goes by X", honorifics such
    as "Mr. X" or "Dr. X Y", and a name on its own at the very end after a
    sign-off ("Thanks, John"), a line ending in a comma, a dash ("-- John")
    or a blank line (Reddit's "- John" reaches here as a bare "John").

    Limitation: names anywhere else ("I met Sarah on Hinge", "Sarah said...")
    are NOT redacted. Telling a first name apart from a capitalised word or a
    brand needs NER, and redacting every capitalised word would destroy the
    platform and company names extraction depends on. Platform names in
    ``PROTECTED_WORDS`` are never treated as names, and CamelCase brands such
    as "CryptoMax" do not match the name pattern at all.
    """
    text = INTRO_NAME_RE.sub(_redact_name, text)
    text = TITLE_NAME_RE.sub(_redact_name, text)
    return SIGNOFF_RE.sub(lambda m: _redact_name(m, SIGNOFF_STOP_WORDS), text)


def redact_pii(text: str) -> str:
    """Remove PII with fixed placeholders, keeping amounts, dates and platforms."""
    text = normalise_cfpb_redactions(text)
    text = redact_contacts(text)
    return redact_names(text)


def clean_text(text: str) -> str:
    """Strip markdown and links, then remove PII."""
    return redact_pii(strip_markdown(text))


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------

REMOVED_RE = re.compile(r"^\[(?:removed|deleted)[^\]]*\]$", re.I)
META_TITLE_RE = re.compile(
    r"^\s*[\[(]?(?:meta|mod|mods|announcement)\b[\])]?\s*[:\-|]?"
    r"|\b(?:mod post|mod announcement|megathread|weekly thread|monthly thread"
    r"|subreddit rules|rules update|state of the sub)\b",
    re.I,
)
BOT_BODY_RE = re.compile(r"\b(?:i am a bot|this action was performed automatically)\b", re.I)
SCREENSHOT_RE = re.compile(
    r"\b(?:screen\s?shots?|screen\s?caps?|pics?|pictures?|images?|photos?|imgur|i\.redd\.it"
    r"|attached|see (?:below|above))\b",
    re.I,
)
SCAM_QUESTION_RE = re.compile(r"\bis (?:this|it|that) (?:a )?(?:scam|legit|real|fake|safe)\b", re.I)


def is_removed_or_empty(body: str) -> bool:
    """True for ``[removed]``, ``[deleted]`` and blank bodies."""
    stripped = body.strip()
    return not stripped or bool(REMOVED_RE.match(stripped))


def is_meta_post(title: str, body: str) -> bool:
    """True for mod announcements, megathreads and bot posts.

    Only the title is checked for meta keywords, because a victim story can
    legitimately mention a "moderator" (fake Discord mods are a common scam).
    """
    return bool(META_TITLE_RE.search(title) or BOT_BODY_RE.search(body))


def link_share(raw_body: str) -> float:
    """Fraction of the raw body taken up by URLs, markdown link targets included."""
    stripped = raw_body.strip()
    if not stripped:
        return 0.0
    url_chars = sum(len(m.group(0)) for m in URL_RE.finditer(stripped))
    return url_chars / len(stripped)


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text))


def is_link_or_screenshot_only(title: str, raw_body: str, clean_body: str) -> bool:
    """True when a post is mostly a pasted link or screenshot with little narrative.

    A post qualifies only if it has a link or an image reference at all. It is
    then dropped when URLs make up at least half the raw body, or when the
    cleaned body is under ``LINK_ONLY_MAX_WORDS`` words. "Is this a scam?"
    titles get a higher bar (``QUESTION_ONLY_MAX_WORDS``) because those posts
    usually ask about the link rather than tell a story.
    """
    has_link = bool(URL_RE.search(raw_body) or MD_LINK_RE.search(raw_body))
    has_image = bool(SCREENSHOT_RE.search(raw_body))
    if not (has_link or has_image):
        return False
    if link_share(raw_body) >= LINK_SHARE_THRESHOLD:
        return True
    limit = QUESTION_ONLY_MAX_WORDS if SCAM_QUESTION_RE.search(title) else LINK_ONLY_MAX_WORDS
    return word_count(clean_body) < limit


def body_fingerprint(body: str) -> str:
    """Hash of the body after lowercasing and collapsing whitespace."""
    normalised = " ".join(body.lower().split())
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()


def build_text(title: str, body: str) -> str:
    """Title, blank line, body; just the body when there is no title."""
    return f"{title}\n\n{body}" if title else body


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def clean_posts(posts: Iterable[dict]) -> tuple[list[dict], Counter]:
    """Clean and filter raw posts. Returns the kept posts and a count per drop reason."""
    stats: Counter = Counter({reason: 0 for reason in DROP_REASONS})
    seen_ids: set[str] = set()
    seen_bodies: set[str] = set()
    kept: list[dict] = []

    for post in posts:
        stats["input"] += 1
        if post["id"] in seen_ids:
            stats["duplicate_id"] += 1
            continue
        seen_ids.add(post["id"])

        raw_title = post.get("title") or ""
        raw_body = post.get("body") or ""
        if is_removed_or_empty(raw_body):
            stats["removed_or_empty"] += 1
            continue
        if is_meta_post(raw_title, raw_body):
            stats["meta_post"] += 1
            continue

        title = clean_text(raw_title)
        body = clean_text(raw_body)
        if is_link_or_screenshot_only(raw_title, raw_body, body):
            stats["link_or_screenshot_only"] += 1
            continue

        text = build_text(title, body)
        if len(text) < MIN_CHARS:
            stats["too_short"] += 1
            continue

        fingerprint = body_fingerprint(body)
        if fingerprint in seen_bodies:
            stats["duplicate_text"] += 1
            continue
        seen_bodies.add(fingerprint)

        kept.append({"id": post["id"], "source": post["source"], "text": text, "n_chars": len(text)})

    stats["output"] = len(kept)
    return kept, stats


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def format_summary(stats: Counter) -> str:
    lines = [f"input posts:   {stats['input']}"]
    lines += [f"  dropped {reason}: {stats[reason]}" for reason in DROP_REASONS]
    lines.append(f"output posts:  {stats['output']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="raw posts JSONL")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="clean posts JSONL")
    args = parser.parse_args(argv)

    kept, stats = clean_posts(read_jsonl(args.input))
    write_jsonl(args.output, kept)
    print(format_summary(stats))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
