"""Run the whole data pipeline: scrape, then clean, then extract.

Each step is the module's own ``main``, run in order. The run stops at the
first step that raises or exits non-zero, names that step and exits non-zero.

Usage::

    python -m pipeline.run [--limit N] [--skip-scrape] [--from-cache-only]

``--limit`` and ``--from-cache-only`` are passed to ``pipeline.extract``.
``--skip-scrape`` reuses the existing ``data/raw/posts.jsonl``.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from pipeline import clean, extract, scrape


def steps(args: argparse.Namespace) -> list[tuple[str, Callable[[], object]]]:
    """The (name, call) pairs to run, in order."""
    extract_argv: list[str] = []
    if args.limit is not None:
        extract_argv += ["--limit", str(args.limit)]
    if args.from_cache_only:
        extract_argv.append("--from-cache-only")

    plan: list[tuple[str, Callable[[], object]]] = []
    if not args.skip_scrape:
        plan.append(("scrape", scrape.main))  # scrape.main takes no arguments
    plan.append(("clean", lambda: clean.main([])))
    plan.append(("extract", lambda: extract.main(extract_argv)))
    return plan


def run_step(name: str, call: Callable[[], object]) -> str | None:
    """Run one step. Return None on success, or a message saying why it failed."""
    try:
        call()
    except SystemExit as exc:
        if exc.code in (None, 0):
            return None
        detail = exc.code if isinstance(exc.code, str) else f"exit code {exc.code}"
        return f"step '{name}' failed: {detail}"
    except Exception as exc:  # noqa: BLE001 - any error stops the run with its name
        return f"step '{name}' failed: {type(exc).__name__}: {exc}"
    return None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None, help="extract only the first N posts")
    parser.add_argument("--skip-scrape", action="store_true",
                        help="reuse data/raw/posts.jsonl instead of scraping again")
    parser.add_argument("--from-cache-only", action="store_true",
                        help="extract from pipeline/.cache only, no API key or network")
    args = parser.parse_args(argv)

    plan = steps(args)
    for i, (name, call) in enumerate(plan, 1):
        print(f"\n=== [{i}/{len(plan)}] {name} ===")
        error = run_step(name, call)
        if error:
            raise SystemExit(f"\npipeline stopped: {error}")
    print("\npipeline finished: " + ", ".join(name for name, _ in plan))


if __name__ == "__main__":
    main()
