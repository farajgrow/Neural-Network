"""Extract structured Story objects from clean posts.

The real path sends each post to Claude Haiku (tool use, see pipeline/prompts.py):

    python -m pipeline.extract --limit 50

For the demo there is no API key. The model replies are instead written into the
cache by hand-assisted extraction, and the stories file is built from the cache
alone, with no network access:

    python -m pipeline.extract --from-cache-only

Replies written outside this module (one JSON object per line: "id" plus the
`record_story` fields below) are added to the cache with:

    python -m pipeline.extract --import replies.jsonl

Post ids in data/exclude.json (near-duplicates and a fictional post removed by
hand) are never turned into stories.

Cache entry format
------------------
Path: pipeline/.cache/<safe post id>-<prompt hash>.json
  - <safe post id> is the post id with every character outside [A-Za-z0-9_.-]
    replaced by "_" (e.g. reddit-abc123 stays reddit-abc123).
  - <prompt hash> is pipeline.prompts.prompt_hash(): the first 12 hex chars of
    sha256 over the model id, system prompt and tool schema. Changing the prompt
    changes the hash, so stale entries are ignored. Print it with
    `python -c "from pipeline.prompts import prompt_hash; print(prompt_hash())"`.
  - extract.cache_path(post_id) returns the full path, and
    `python -m pipeline.extract --print-cache-key <post_id>` prints it.
  - With --from-cache-only, posts with no cache entry are skipped and counted
    in the dropped total ("not in cache").
Content: one UTF-8 JSON object, the `record_story` tool input (no id/source):
    {
      "scam_type": "<vocab scam_types>",
      "events": [{"step": "<vocab steps>", "actor": "<vocab actors>",
                  "detail": "short phrase"}, ...],          # chronological, >= 1
      "outcome": "lost_money" | "escaped" | "unknown",
      "amount_lost_usd": <number> | null,
      "snippet": "1 to 2 sentence anonymised summary"
    }
id and source are taken from data/clean/posts.jsonl. On build, details and the
snippet are PII-redacted, an amount <= 0 becomes null, and outcome is forced to
lost_money when there is an amount or a money_lost event. The result is then
validated against contract/story.schema.json; failures are dropped and printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
from pathlib import Path
from typing import Any

import jsonschema

from pipeline.clean import read_jsonl, redact_pii
from pipeline.prompts import (
    EXTRACT_MODEL,
    STORY_TOOL,
    SYSTEM_PROMPT,
    TOOL_NAME,
    prompt_hash,
    user_message,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = Path("data/clean/posts.jsonl")
DEFAULT_OUTPUT = Path("data/stories.json")
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
SCHEMA_PATH = REPO_ROOT / "contract" / "story.schema.json"
EXCLUDE_PATH = REPO_ROOT / "data" / "exclude.json"

CONCURRENCY = 8
MAX_RETRIES = 4
MAX_TOKENS = 1500

_SCHEMA = json.loads(SCHEMA_PATH.read_text())
STORY_VALIDATOR = jsonschema.Draft202012Validator(
    {**_SCHEMA["$defs"]["story"], "$defs": _SCHEMA["$defs"]}
)


def load_api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            m = re.match(r"\s*(?:export\s+)?ANTHROPIC_API_KEY\s*=\s*(.*)", line)
            if m:
                return m.group(1).strip().strip("'\"") or None
    return None


def load_exclude(path: Path = EXCLUDE_PATH) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text())["ids"])


def import_replies(path: Path, posts: list[dict], cache_dir: Path = CACHE_DIR) -> tuple[int, list[tuple[str, str]]]:
    """Write replies from a JSONL file into the cache. Return (written, rejected (id, reason))."""
    by_id = {p["id"]: p for p in posts}
    written, rejected = 0, []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            rejected.append(("?", f"bad JSON: {exc}"))
            continue
        post_id = raw.pop("id", None)
        if post_id not in by_id:
            rejected.append((str(post_id), "no clean post with this id"))
            continue
        err = validation_error(build_story(by_id[post_id], raw))
        if err:
            rejected.append((post_id, f"invalid: {err}"))
            continue
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path(post_id, cache_dir).write_text(json.dumps(raw, ensure_ascii=False))
        written += 1
    return written, rejected


class CacheMiss(Exception):
    pass


def cache_path(post_id: str, cache_dir: Path = CACHE_DIR) -> Path:
    safe = re.sub(r"[^\w.-]", "_", post_id)
    return cache_dir / f"{safe}-{prompt_hash()}.json"


def _anonymise(text: str) -> str:
    return re.sub(r"\s+", " ", redact_pii(text)).strip()


def build_story(post: dict, raw: dict) -> dict:
    """Combine the model's tool input with post metadata and enforce contract rules."""
    events = [
        {
            "step": e.get("step"),
            "actor": e.get("actor"),
            "detail": _anonymise(str(e.get("detail", ""))),
        }
        for e in raw.get("events") or []
        if isinstance(e, dict)
    ]
    amount = raw.get("amount_lost_usd")
    if isinstance(amount, str):
        try:
            amount = float(amount.replace(",", "").replace("$", ""))
        except ValueError:
            amount = None
    if isinstance(amount, (int, float)) and amount <= 0:
        amount = None
    outcome = raw.get("outcome")
    if amount is not None or any(e["step"] == "money_lost" for e in events):
        outcome = "lost_money"
    return {
        "id": post["id"],
        "source": post["source"],
        "scam_type": raw.get("scam_type"),
        "events": events,
        "outcome": outcome,
        "amount_lost_usd": amount,
        "snippet": _anonymise(str(raw.get("snippet", ""))),
    }


def validation_error(story: dict) -> str | None:
    err = jsonschema.exceptions.best_match(STORY_VALIDATOR.iter_errors(story))
    if err is None:
        return None
    path = "/".join(str(p) for p in err.absolute_path) or "<root>"
    return f"{path}: {err.message}"


async def call_model(client: Any, text: str) -> dict:
    resp = await client.messages.create(
        model=EXTRACT_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=[STORY_TOOL],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": user_message(text)}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            return dict(block.input)
    raise ValueError("response had no tool_use block")


async def fetch_raw(
    client: Any, post: dict, sem: asyncio.Semaphore, cache_dir: Path, stats: dict
) -> dict:
    path = cache_path(post["id"], cache_dir)
    if path.exists():
        stats["cached"] += 1
        return json.loads(path.read_text())
    if client is None:
        raise CacheMiss(f"no cache entry {path.name}")
    async with sem:
        delay = 1.0
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                raw = await call_model(client, post["text"])
                break
            except Exception:
                if attempt == MAX_RETRIES:
                    raise
                await asyncio.sleep(delay + random.random() * 0.5)
                delay *= 2
    stats["api_calls"] += 1
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False))
    return raw


async def extract_posts(
    posts: list[dict], client: Any, cache_dir: Path = CACHE_DIR, concurrency: int = CONCURRENCY
) -> tuple[list[dict], list[tuple[str, str]], dict]:
    """client=None means cache only: no network, missing entries are dropped.

    Return (valid stories in input order, dropped (id, reason) pairs, stats)."""
    sem = asyncio.Semaphore(concurrency)
    stats = {"cached": 0, "api_calls": 0}
    results = await asyncio.gather(
        *(fetch_raw(client, p, sem, cache_dir, stats) for p in posts), return_exceptions=True
    )
    stories, dropped = [], []
    for post, raw in zip(posts, results):
        if isinstance(raw, BaseException):
            if isinstance(raw, CacheMiss):
                dropped.append((post["id"], f"not in cache: {raw}"))
                continue
            dropped.append((post["id"], f"api error: {type(raw).__name__}: {raw}"))
            continue
        story = build_story(post, raw)
        err = validation_error(story)
        if err:
            dropped.append((post["id"], f"invalid: {err}"))
        else:
            stories.append(story)
    return stories, dropped, stats


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--limit", type=int, default=None, help="only the first N posts")
    ap.add_argument("--concurrency", type=int, default=CONCURRENCY)
    ap.add_argument("--from-cache-only", action="store_true",
                    help="build stories purely from pipeline/.cache, no API key or network")
    ap.add_argument("--import", dest="import_path", type=Path, metavar="JSONL",
                    help="add replies from a JSONL file to the cache and exit")
    ap.add_argument("--print-cache-key", metavar="POST_ID",
                    help="print the expected cache file path for a post id and exit")
    args = ap.parse_args(argv)

    if args.print_cache_key:
        print(cache_path(args.print_cache_key))
        return

    posts = read_jsonl(args.input)
    if args.import_path:
        written, rejected = import_replies(args.import_path, posts)
        print(f"cached {written} replies from {args.import_path}, rejected {len(rejected)}")
        for post_id, reason in rejected:
            print(f"  rejected {post_id}: {reason}")
        return

    exclude = load_exclude()
    posts = [p for p in posts if p["id"] not in exclude]
    if args.limit is not None:
        posts = posts[: args.limit]

    if args.from_cache_only:
        client = None
    else:
        key = load_api_key()
        if not key:
            raise SystemExit(
                "ANTHROPIC_API_KEY not set (environment or .env at repo root); "
                "use --from-cache-only to build from pipeline/.cache"
            )
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=key, max_retries=2)
    stories, dropped, stats = asyncio.run(
        extract_posts(posts, client, concurrency=args.concurrency)
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(stories, indent=2, ensure_ascii=False) + "\n")

    print(f"posts: {len(posts)}  api calls: {stats['api_calls']}  cached: {stats['cached']}")
    print(f"valid stories: {len(stories)}  dropped: {len(dropped)}")
    for post_id, reason in dropped:
        print(f"  dropped {post_id}: {reason}")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
