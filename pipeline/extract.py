"""Extract a structured Story from every clean post with Claude Haiku.

Reads ``data/clean/posts.jsonl`` (written by ``pipeline.clean``, format in
``pipeline/README.md``) and writes ``data/stories.json``, a JSON array that
conforms to ``contract/story.schema.json``.

For each post:

1. Look up the cache (``data/cache/extract/``). The key covers the post id,
   the post text and a hash of the prompt, schema and model settings, so a
   rerun is free and any prompt change re-extracts everything.
2. On a miss, ask the model for a Story via structured output. Requests run
   concurrently (``--concurrency``); the SDK retries rate limits, overload and
   server errors with backoff.
3. Fill in ``id`` and ``source`` from the post, scrub PII from the text fields
   as a safety net, then validate against the contract with ``jsonschema``.
   Only valid stories are cached and written.

Prints ok / invalid / API error counts at the end.

Usage::

    python -m pipeline.extract [--limit N] [--input ...] [--output ...]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic

from pipeline.clean import redact_pii
from pipeline.prompts import (
    EXTRACT_MODEL,
    EXTRACTION_SYSTEM_PROMPT,
    extraction_output_schema,
    extraction_user_message,
    prompt_hash,
    stories_file_validator,
    story_validator,
)

DEFAULT_INPUT = Path("data/clean/posts.jsonl")
DEFAULT_OUTPUT = Path("data/stories.json")
DEFAULT_CACHE_DIR = Path("data/cache/extract")

DEFAULT_CONCURRENCY = 12
MAX_RETRIES = 6

# Everything here shapes the output, so all of it goes into the cache key.
REQUEST_PARAMS: dict[str, Any] = {
    "model": EXTRACT_MODEL,
    "max_tokens": 4096,
    "system": EXTRACTION_SYSTEM_PROMPT,
    "output_config": {
        "effort": "low",
        "format": {"type": "json_schema", "schema": extraction_output_schema()},
    },
}
PROMPT_HASH = prompt_hash(REQUEST_PARAMS)

# Order matters: this is also the order of the summary printed by main().
STATUSES = ("ok", "invalid", "api_error")


class ModelOutputError(Exception):
    """The model answered, but not with a usable Story (refusal, cut off, bad JSON)."""


@dataclass
class Result:
    post_id: str
    status: str
    story: dict[str, Any] | None = None
    error: str = ""
    cached: bool = False


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")


def cache_path(cache_dir: Path, post: dict[str, Any]) -> Path:
    key = prompt_hash(PROMPT_HASH, post["text"])[:16]
    return cache_dir / f"{SAFE_ID_RE.sub('_', post['id'])}-{key}.json"


def read_cache(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_cache(path: Path, output: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    tmp.replace(path)


# --------------------------------------------------------------------------
# Story building and validation
# --------------------------------------------------------------------------


def build_story(post: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    """Turn the model's output into a contract Story for ``post``.

    ``id`` and ``source`` always come from the post. The free-text fields go
    through the cleaner's PII redaction again in case the model copied any.
    """
    story = {"id": post["id"], "source": post["source"], **output}
    if isinstance(story.get("snippet"), str):
        story["snippet"] = redact_pii(story["snippet"]).strip()
    for event in story.get("events") or []:
        if isinstance(event, dict) and isinstance(event.get("detail"), str):
            event["detail"] = redact_pii(event["detail"]).strip()
    return story


def validation_errors(story: dict[str, Any]) -> list[str]:
    errors = sorted(story_validator().iter_errors(story), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors]


# --------------------------------------------------------------------------
# Model calls
# --------------------------------------------------------------------------


async def call_model(client: anthropic.AsyncAnthropic, text: str) -> dict[str, Any]:
    response = await client.messages.create(
        **REQUEST_PARAMS,
        messages=[{"role": "user", "content": extraction_user_message(text)}],
    )
    if response.stop_reason != "end_turn":
        raise ModelOutputError(f"stop_reason={response.stop_reason}")
    body = next((block.text for block in response.content if block.type == "text"), None)
    if body is None:
        raise ModelOutputError("no text block in response")
    try:
        output = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ModelOutputError(f"invalid JSON: {exc}") from exc
    if not isinstance(output, dict):
        raise ModelOutputError("output is not a JSON object")
    return output


async def extract_post(
    client: anthropic.AsyncAnthropic,
    post: dict[str, Any],
    cache_dir: Path,
    semaphore: asyncio.Semaphore,
) -> Result:
    path = cache_path(cache_dir, post)
    output = read_cache(path)
    cached = output is not None
    if output is None:
        async with semaphore:
            try:
                output = await call_model(client, post["text"])
            except anthropic.APIError as exc:
                return Result(post["id"], "api_error", error=f"{type(exc).__name__}: {exc}")
            except ModelOutputError as exc:
                return Result(post["id"], "invalid", error=str(exc))

    story = build_story(post, output)
    errors = validation_errors(story)
    if errors:
        return Result(post["id"], "invalid", error="; ".join(errors[:3]), cached=cached)
    if not cached:
        write_cache(path, output)
    return Result(post["id"], "ok", story=story, cached=cached)


async def extract_all(
    posts: list[dict[str, Any]],
    client: anthropic.AsyncAnthropic,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[Result]:
    """Extract every post, in input order, with at most ``concurrency`` calls in flight."""
    semaphore = asyncio.Semaphore(concurrency)
    return await asyncio.gather(
        *(extract_post(client, post, cache_dir, semaphore) for post in posts)
    )


# --------------------------------------------------------------------------
# I/O and CLI
# --------------------------------------------------------------------------


def read_posts(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    posts = []
    with path.open() as f:
        for line in f:
            if line.strip():
                posts.append(json.loads(line))
            if limit is not None and len(posts) >= limit:
                break
    return posts


def write_stories(path: Path, stories: list[dict[str, Any]]) -> None:
    stories_file_validator().validate(stories)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(stories, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def summarise(results: Iterable[Result]) -> Counter[str]:
    return Counter(result.status for result in results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--limit", type=int, default=None, help="only the first N posts")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    args = parser.parse_args(argv)

    posts = read_posts(args.input, args.limit)
    print(f"read {len(posts)} posts from {args.input}")

    n_uncached = sum(read_cache(cache_path(args.cache_dir, p)) is None for p in posts)
    if n_uncached and not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"ANTHROPIC_API_KEY is not set and {n_uncached} posts aren't cached", file=sys.stderr)
        return 2

    async def run() -> list[Result]:
        async with anthropic.AsyncAnthropic(max_retries=MAX_RETRIES) as client:
            return await extract_all(posts, client, args.cache_dir, args.concurrency)

    results = asyncio.run(run())

    for result in results:
        if result.status != "ok":
            print(f"  {result.status:9} {result.post_id}: {result.error}", file=sys.stderr)

    counts = summarise(results)
    n_cached = sum(result.cached for result in results if result.status == "ok")
    print(f"extracted {len(posts)} posts ({n_cached} ok from cache):")
    for status in STATUSES:
        print(f"  {status:9} {counts[status]:5}")

    stories = [result.story for result in results if result.story is not None]
    if not stories:
        print(f"no valid stories; leaving {args.output} untouched", file=sys.stderr)
        return 1
    write_stories(args.output, stories)
    print(f"wrote {len(stories)} stories to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
