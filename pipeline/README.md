# Pipeline

scrape → clean → extract → `data/stories.json`

Run each step with `.venv/bin/python -m pipeline.<module>` from the repo root, or all of them at once (see below).

## Run it

```bash
.venv/bin/python -m pipeline.run                    # scrape, clean, extract (needs ANTHROPIC_API_KEY)
.venv/bin/python -m pipeline.run --limit 50         # extract only the first 50 clean posts
.venv/bin/python -m pipeline.run --skip-scrape      # reuse data/raw/posts.jsonl
.venv/bin/python -m pipeline.run --skip-scrape --from-cache-only   # no network, no API key
```

`pipeline.run` calls each module's `main` in order. It stops at the first step that raises or exits non-zero, prints `pipeline stopped: step '<name>' failed: ...` and exits non-zero. `--limit` and `--from-cache-only` are passed to `pipeline.extract`. Without an API key, extract stops the run unless `--from-cache-only` is given.

Tests (no network, no API key):

```bash
.venv/bin/python -m pytest -q pipeline/tests
```

## Data

### Sources

`pipeline.scrape` writes `data/raw/posts.jsonl`. It keeps only posts whose body has at least 400 characters and is not `[removed]` or `[deleted]`, and it stops topping up at 700 posts. Sources, in the order they are tried:

1. **Reddit's public JSON listing** for r/Scams (top of all time, 100 posts). Usually blocked, so it is tried once without retries.
2. **[Arctic Shift](https://arctic-shift.photon-reddit.com)**, a Reddit archive API, in three passes over r/Scams:
   - keyword searches per scam type (for example "pig butchering", "task job", "IRS", "zelle", "romance"), up to 35 usable posts per query;
   - a high-score slice: one month every two months from January 2018 to now, keeping the 3 highest-scoring usable posts from each;
   - the newest posts, to top up the total.
3. **CFPB consumer complaint narratives** that mention "scam" or "fraud", up to 150. If the API returns complaints without narrative text, CFPB is skipped.

Requests share a limit of one request per second and retry with backoff (5, 10, 20, 40 s) on HTTP 429, 5xx, network errors and Arctic Shift's 422 "Timeout" overload reply. Posts are deduplicated by id across sources.

All 487 stories in the current `data/stories.json` come from Reddit through Arctic Shift; CFPB contributed none. Per source (the `fetched_via` field):

| Source | Raw posts | Stories |
|---|---|---|
| `arctic:new` (newest posts) | 200 | 195 |
| `arctic:top` (high-score slice) | 135 | 134 |
| `arctic:task job` | 40 | 39 |
| `arctic:remote job recruiter` | 40 | 39 |
| `arctic:IRS` | 40 | 40 |
| `arctic:zelle` | 40 | 40 |
| **Total** | **495** | **487** |

The current `data/raw/posts.jsonl` predates the query list above: only four keyword queries appear in it, at 40 posts each rather than 35.

### Counts

| Stage | Posts | Notes |
|---|---|---|
| Scraped (`data/raw/posts.jsonl`) | 495 | |
| Cleaned (`data/clean/posts.jsonl`) | 493 | 2 dropped by the filters below: 1 meta post and 1 exact duplicate body |
| Stories (`data/stories.json`) | 487 | 5 near-duplicates and 1 fictional post removed. TODO: say how they were found and removed (by hand or a script) |

TODO: extraction split: ok / invalid (failed schema validation) / api error / not in cache.

What the 487 stories contain (from `data/stories.json`):

- scam types: other 170, fake_job 105, impersonation 102, marketplace 67, romance 26, pig_butchering 17
- outcomes: unknown 210, escaped 176, lost_money 101
- 1,615 events (about 3.3 per story); 60 stories give an amount lost (median $1,000)

### Cleaning

`pipeline.clean` reads the raw posts and, for each one, in order:

1. Drops repeated ids.
2. Drops removed, deleted or empty bodies.
3. Drops mod and meta posts (title keywords such as "megathread" or "[Mod]") and bot posts.
4. Strips Markdown (headings, emphasis, quotes, lists, code, spoilers, HTML entities) and links: `[text](url)` keeps the text, and images and bare URLs are removed.
5. Removes PII with fixed placeholders: `[email]`, `[phone]`, `[wallet]` (Ethereum, Bitcoin, Tron), `[number]` (card, SSN, long numbers), `[user]` (`u/name`, `@handle`) and `[name]`. Dollar amounts, dates and platform names (WhatsApp, Zelle, Coinbase, ...) are kept because extraction needs them. CFPB's own `XXXX` redactions become `[redacted]`, `[date]` or `[number]`.
6. Drops posts that share a link or a screenshot but tell little story: URLs make up half the body, or fewer than 50 words are left (80 for "is this a scam?" titles).
7. Drops posts shorter than 300 characters after cleaning.
8. Drops exact duplicate bodies (crossposts and reposts), compared after lowercasing and collapsing whitespace.

It prints the number dropped at each filter. Limitation: names are only redacted in fixed patterns ("my name is X", "Mr. X", a name in a sign-off). A name elsewhere in the text ("I met Sarah on Hinge") is kept, since catching it would need NER. Snippets and event details are redacted again when stories are built.

### Extraction

`pipeline.extract` turns each clean post into a Story (see `contract/story.schema.json`):

- Model: Claude Haiku (`claude-haiku-5-5`, set in `pipeline/prompts.py`), with forced tool use: the model must call a `record_story` tool whose input schema uses the fixed step, scam type, actor and outcome lists from `contract/vocab.json`.
- 8 requests at a time, 4 attempts per post with backoff.
- Every reply is cached in `pipeline/.cache/<post id>-<prompt hash>.json`. The hash covers the model, system prompt and tool schema, so reruns are free and a prompt change starts a fresh cache.
- When a story is built, details and snippet are PII-redacted again, an amount of 0 or less becomes null, and the outcome is set to `lost_money` whenever there is an amount or a `money_lost` event. Each story is then validated against the contract schema; failures are dropped and printed with the reason.
- `--from-cache-only` builds the stories file from the cache alone, with no API key or network. Posts with no cache entry are dropped and counted.

The current `data/stories.json` was built with `--from-cache-only`. No API key was available, so the cache entries were written by hand-assisted extraction in the `record_story` format rather than by live Haiku calls (see the docstring in `pipeline/extract.py`). TODO: confirm this wording with Arya.

### Accuracy

`python -m pipeline.spotcheck` shows a seeded sample of 20 stories next to their clean posts and asks, for each one, whether `scam_type`, `events`, `outcome` and `amount_lost_usd` are right. `--report` prints accuracy per field and overall.

Spot-check accuracy (n = 20). These checks were made by a model review of each story against its post, not by hand (every row in `data/spotcheck/results.jsonl` is noted "model review"):

| Field | Correct |
|---|---|
| `scam_type` | 100% (19/19; one story was marked unsure, as it could be a real debt collector) |
| `events` | 90% (18/20) |
| `outcome` | 90% (18/20) |
| `amount_lost_usd` | 100% (20/20) |
| All four fields right | 85% (17/20) |
| Overall (field checks) | 94.9% (75/79) |

### Caveats

The stories are self-reported, so they skew toward people who posted about a scam, and many end before the outcome is known (210 of 487 are `unknown`). Read the graphs as patterns associated with loss, not causes.

## Matcher

`pipeline.matcher.match(text)` returns a dict valid against `contract/matcher.schema.json`. Try it with `python -m pipeline.matcher "text"`; the path that answered is printed to stderr. It tries these in order and falls through on any error or invalid answer:

1. The disk cache in `pipeline/.cache/matcher/` (gitignored). Only valid model answers are cached.
2. **Jev** (TypeSafe System One, `jev-latest`), when `TYPESAFE_API_KEY` is set. One request asks a Choice for the scam type, a Choice for the scammer's next move, a Noul for "is this a scam at all" and a Noul per step. For the scammer's own moves (platform switch, urgency and so on), the step counts when the scammer does it or asks for it. For the person's actions (paying, sharing codes, remote access), it counts only when they have done it. Code then builds the result: steps above 0.5 count as seen and are ordered by where they usually fall in the real stories. The next step is the move Jev rates most likely among those not seen yet; victim reactions such as `victim_suspicious` are never predicted, and the most common transition in `data/stories.json` is the fallback. `confidence` is P(scam type) × P(scam), and the advice comes from fixed templates. Below 0.5 on "is this a scam", the result is `other` with low confidence.
3. **Sonnet** (`claude-sonnet-5-5`, forced tool use), when `ANTHROPIC_API_KEY` is set.
4. The stored answers in `demo_responses.json`, if the text is one of the two demo messages.
5. The offline regex rules.

Keys are read from the environment or a repo-root `.env` (gitignored) and are never logged. Tests mock every API call; the live checks run only when a key is set: `python -m pytest -q pipeline/tests/test_matcher.py -k live`.

## Intermediate files (gitignored, local only)

### `data/raw/posts.jsonl`: written by `pipeline.scrape`

Each line is one post:

| field | type | notes |
|---|---|---|
| `id` | str | `reddit-<post id>` or `cfpb-<complaint id>`; unique |
| `source` | `"reddit"` or `"cfpb"` | |
| `source_id` | str | raw id from the source |
| `title` | str | `""` when the source has none |
| `body` | str | raw post text or complaint narrative, untouched |
| `created_utc` | int | unix seconds |
| `score` | int or null | Reddit score; null for CFPB |
| `url` | str | permalink, or CFPB complaint URL |
| `fetched_via` | str | which query or listing found it, e.g. `arctic:top`, `cfpb:pig butchering` |

### `data/clean/posts.jsonl`: written by `pipeline.clean`

Each line is one post that passed the filters:

| field | type | notes |
|---|---|---|
| `id` | str | same as raw |
| `source` | str | same as raw |
| `text` | str | cleaned title + blank line + cleaned body; markdown, links and PII removed |
| `n_chars` | int | `len(text)` |

`pipeline.clean` also prints a count of posts dropped at each filter.

## Spot-check (committed)

### `data/spotcheck/results.jsonl`: written by `pipeline.spotcheck`

`python -m pipeline.spotcheck` shows a seeded sample of 20 stories next to their clean posts and asks whether each field is right. It saves after every story, so it can be stopped and resumed. `--report` prints the accuracy for the README.

Each line is one checked story:

| field | type | notes |
|---|---|---|
| `id` | str | story id |
| `checks` | object | `scam_type`, `events`, `outcome`, `amount_lost_usd`: `true`, `false`, or `null` for can't tell |
| `note` | str | free text, `""` if none |
