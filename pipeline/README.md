# Pipeline

scrape → clean → select → extract → `data/stories.json`

Run each step with `.venv/bin/python -m pipeline.<module>` from the repo root, or all of them at once (see below).

## Run it

```bash
.venv/bin/python -m pipeline.run                    # scrape, clean, extract (needs ANTHROPIC_API_KEY)
.venv/bin/python -m pipeline.run --limit 50         # extract only the first 50 clean posts
.venv/bin/python -m pipeline.run --skip-scrape      # reuse data/raw/posts.jsonl
.venv/bin/python -m pipeline.run --skip-scrape --from-cache-only   # no network, no API key
```

`pipeline.run` calls each module's `main` in order. It stops at the first step that raises or exits non-zero, prints `pipeline stopped: step '<name>' failed: ...` and exits non-zero. `--limit` and `--from-cache-only` are passed to `pipeline.extract`. Without an API key, extract stops the run unless `--from-cache-only` is given.

Adding stories from more subreddits (no API key: replies are written outside the pipeline and imported):

```bash
.venv/bin/python -m pipeline.scrape --subreddits-only   # top up SUBREDDIT_SOURCES, keep existing posts
.venv/bin/python -m pipeline.clean
.venv/bin/python -m pipeline.select_posts --total 1000  # writes data/clean/selected.jsonl
.venv/bin/python -m pipeline.extract --import replies.jsonl   # one JSON object per line: "id" + record_story fields
.venv/bin/python -m pipeline.extract --from-cache-only
```

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
3. **CFPB consumer complaint narratives** that mention "scam" or "fraud", up to 150. If the API returns complaints without narrative text, CFPB is skipped. As of September 2026 CFPB has removed narratives from the API and the bulk download, so this step adds nothing. The CFPB posts in the stories come from step 5 instead.
4. **Other scam subreddits** through Arctic Shift (`SUBREDDIT_SOURCES` in `pipeline/scrape.py`), each up to its own quota:
   - whole subreddits, sampled over one-month windows since January 2021 (highest score first in each window): r/Romancescam, r/PigButchering, r/CryptoScams, r/scammers;
   - keyword searches: r/Scams (pig butchering, crypto investment, romance, dating, job and impersonation terms), and "scam" or "scammed" in r/FacebookMarketplace, r/recruitinghell, r/jobs, r/legaladvice and r/personalfinance.

   Subreddits were chosen from a survey of 20 long posts each. paypal, Banking, WorkOnline, IdentityTheft and zelle were left out because few of their long posts were scam stories. A subreddit is skipped after 3 time windows in a row get no answer.

The scrape appends: posts already in `data/raw/posts.jsonl` are kept, and posts already scraped from a subreddit count toward its quota, so a rerun only tops up. Because of this, the `arctic:new` top-up to 700 posts adds nothing once the file holds 700 or more posts. Every raw post now records its `subreddit`; older rows without one are r/Scams.

5. **An archived copy of the CFPB bulk file** (`pipeline.cfpb_bulk`). The Wayback Machine snapshot of 2026-08-15 of `complaints.csv.zip` (1.4 GB zip) still has the "Consumer complaint narrative" column. The script streams the zip row by row and keeps a complaint when its narrative is at least 400 characters and either:
   - its Issue is "Fraud or scam" and its Product is a money transfer, virtual currency or prepaid card product; or
   - its Product is a bank account or credit card product and the narrative says scammer, scammed, fraudster or "fell victim".

   Credit reporting, debt collection, mortgage, student loan and credit repair products are excluded. Each kept complaint gets a keyword bucket (pig_butchering, romance, fake_job, impersonation, marketplace, other), and at most 200 per bucket are taken in a seeded order, so rare buckets are not crowded out. Of 17,119,590 rows: 14,400,564 had a short or missing narrative, 2,177,288 were an excluded product and 513,051 were not scam-related; 28,687 matched and 1,176 were added (`fetched_via` `cfpb:wayback-2026-08-15:<bucket>`).

Requests share a limit of one request per second and retry with backoff (5, 10, 20, 40 s) on HTTP 429, 5xx, network errors and Arctic Shift's 422 "Timeout" overload reply. Posts are deduplicated by id across sources.

The 2,074 stories in the current `data/stories.json` come from Reddit through Arctic Shift (1,774) and the archived CFPB file (300). Stories per source:

| Source | Stories |
|---|---|
| r/Scams (all passes, including the older `arctic:*` rows) | 868 |
| r/Romancescam | 227 |
| r/CryptoScams | 201 |
| r/recruitinghell | 159 |
| r/legaladvice | 71 |
| r/jobs | 70 |
| r/scammers | 61 |
| r/FacebookMarketplace | 51 |
| r/personalfinance | 49 |
| r/PigButchering | 17 |
| CFPB archive: other 55, romance 53, pig_butchering 50, marketplace 50, fake_job 46, impersonation 46 | 300 |
| **Total** | **2,074** |

The first 487 stories came from the earlier r/Scams scrape (`arctic:new` 195, `arctic:top` 134, and keyword queries). 5 of them are now in `data/exclude.json`, which leaves 482.

### Counts

| Stage | Posts | Notes |
|---|---|---|
| Scraped (`data/raw/posts.jsonl`) | 3,272 | 495 from the first r/Scams scrape, 1,601 from the other subreddits, 1,176 from the CFPB archive |
| Cleaned (`data/clean/posts.jsonl`) | 3,240 | 32 dropped: 2 meta posts, 9 link or screenshot only, 21 exact duplicate bodies |
| Selected (`data/clean/selected.jsonl`) | 1,600 | 1,300 Reddit and 300 CFPB, from the posts not yet extracted. 853 were over a quota; 194 had no narrative, 59 were not first-person, 9 were only a question, 4 were a guide or PSA; 28 were near-duplicates and 11 were in `data/exclude.json` |
| Stories (`data/stories.json`) | 2,074 | 482 earlier stories (487 less 5 in `data/exclude.json`) plus 1,592 new. 8 selected posts are held out for a manual look (child-safety content or not a scam story) |

Extraction results for the 1,600 selected posts: 1,592 valid stories, 0 replies rejected by the schema check, 0 API errors. 2 replies came back with the wrong post id and were corrected by hand.

What the 2,074 stories contain (from `data/stories.json`):

- scam types: other 611, fake_job 389, romance 297, impersonation 295, pig_butchering 264, marketplace 218
- outcomes: unknown 741, lost_money 781, escaped 552
- 7,077 events (about 3.4 per story); 490 stories give an amount lost (median $2,723)

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

### Selection

`pipeline.select_posts` picks which new clean posts to extract and writes `data/clean/selected.jsonl` (clean post fields plus `subreddit`). It skips a post when:

1. it already has an extraction cache entry, or its id is in `data/exclude.json`;
2. it doesn't read as a first-person story of events: no first-person words, fewer than 5 past-tense verbs, mostly questions, or a PSA/guide title. Posts from the general subreddits (recruitinghell, jobs, legaladvice, personalfinance, FacebookMarketplace) must also name a scam;
3. it is a near-duplicate of a post already extracted or selected (5-word shingles, Jaccard ≥ 0.5 or containment ≥ 0.8);
4. its subreddit has reached its quota, or the total is reached.

Posts are taken in a seeded order, so reruns pick the same posts. Only posts found by `SUBREDDIT_SOURCES` count toward a quota, plus CFPB narratives from the archived bulk file (`fetched_via` starting `cfpb:wayback`), which share one bucket of `CFPB_QUOTA` posts.

`data/exclude.json` lists posts kept out of the stories: the 6 removed by hand (5 near-duplicates and the fictional `reddit-1x9su20`) and the later post of 5 more same-title repost pairs. `pipeline.extract` skips these ids.

### Extraction

`pipeline.extract` turns each clean post into a Story (see `contract/story.schema.json`):

- Model: Claude Haiku (`claude-haiku-5-5`, set in `pipeline/prompts.py`), with forced tool use: the model must call a `record_story` tool whose input schema uses the fixed step, scam type, actor and outcome lists from `contract/vocab.json`.
- 8 requests at a time, 4 attempts per post with backoff.
- Every reply is cached in `pipeline/.cache/<post id>-<prompt hash>.json`. The hash covers the model, system prompt and tool schema, so reruns are free and a prompt change starts a fresh cache.
- When a story is built, details and snippet are PII-redacted again, an amount of 0 or less becomes null, and the outcome is set to `lost_money` whenever there is an amount or a `money_lost` event. Each story is then validated against the contract schema; failures are dropped and printed with the reason.
- `--from-cache-only` builds the stories file from the cache alone, with no API key or network. Posts with no cache entry are dropped and counted.

The current `data/stories.json` was built with `--from-cache-only`. No API key was available, so none of the cache entries are live Haiku calls. They are model replies in the `record_story` format loaded with `--import`:

- the first 487 stories: hand-assisted extraction (see the docstring in `pipeline/extract.py`);
- the next 300: batches of 50 posts labelled with the instructions in `pipeline/prompts.py` by Gemini 3.8 Flash (150) and Claude Sonnet 4.6 (150), through an outside agent tool;
- the last 1,300: batches of 50 labelled by Claude Haiku subagents with the same instructions.

Every batch went through the same schema check, a check that each reply matches its own post, and a read of 5 random stories per batch. Models differ, so labelling style is not identical across batches.

### Accuracy

`python -m pipeline.spotcheck` shows a seeded sample of 20 stories next to their clean posts and asks, for each one, whether `scam_type`, `events`, `outcome` and `amount_lost_usd` are right. `--report` prints accuracy per field and overall.

Spot-check accuracy (n = 20, the same seeded sample both times). The hand check is in `data/spotcheck/results_hand.jsonl` (`--results data/spotcheck/results_hand.jsonl --report`). An earlier model review of the same stories is in `data/spotcheck/results.jsonl`:

| Field | By hand | Model review |
|---|---|---|
| `scam_type` | 95% (19/20) | 100% (19/19; one marked unsure, as it could be a real debt collector) |
| `events` | 100% (20/20) | 90% (18/20) |
| `outcome` | 95% (19/20) | 90% (18/20) |
| `amount_lost_usd` | 95% (19/20) | 100% (20/20) |
| All four fields right | 95% (19/20) | 85% (17/20) |
| Overall (field checks) | 96.2% (77/80) | 94.9% (75/79) |

The two checks disagree on 5 stories. By hand, the only miss was the PayPal "mistaken payment" story (`reddit-ckg3bq`), wrong on scam type, outcome and amount. The model review accepted that one but was stricter on four others, about which payment step and outcome labels fit.

On the new stories, a model review of 20 seeded stories drawn from the 1,592 added since the first 487 is in `data/spotcheck/results_new_model.jsonl` (`python -m pipeline.spotcheck --results data/spotcheck/results_new_model.jsonl --report`). It is separate from the hand check above and has not been checked by hand:

| Field | Model review of new stories |
|---|---|
| `scam_type` | 100% (20/20) |
| `events` | 85% (17/20) |
| `outcome` | 100% (20/20) |
| `amount_lost_usd` | 100% (20/20) |
| All four fields right | 85% (17/20) |
| Overall (field checks) | 96.2% (77/80) |

The three `events` misses were a wrong step label (a paid-priority offer marked as `victim_suspicious`), a missing pressure step and unlisted fee payments. This sample is small, so each figure carries a wide margin.

### Caveats

The stories are self-reported, so they skew toward people who posted about a scam, and many end before the outcome is known (741 of 2,074 are `unknown`). Read the graphs as patterns associated with loss, not causes.

- **Selection lets some non-scam posts through.** Reddit posts from the general subreddits (jobs, legaladvice, personalfinance, recruitinghell) and a few CFPB account disputes are not scam stories. They come out as `other` with an `unknown` outcome and one placeholder event. Filter on `outcome` and event count if you need clean scam sequences.
- **Amounts are the victim's stated loss, roughly.** Models converted other currencies at rough rates, took lower bounds ("over $100,000" as 100,000) and sometimes used a total from the post that is not one victim's loss. A few amounts of $500,000 or more are unchecked.
- **CFPB is a frozen archive.** CFPB pulled narratives in September 2026, so these 300 stories are a snapshot taken on 2026-08-15 that skews to US banks and payment apps. They are written as short formal complaints, not the casual first-person posts on Reddit.
- **Mixed labellers.** Three model families labelled different batches; see the Extraction section.
- **Names.** Redaction uses fixed patterns, so a name elsewhere in a post can survive in the text, though snippets and event details were scanned for placeholders, emails, handles and phone numbers.


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
