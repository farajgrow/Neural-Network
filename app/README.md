# app

FastAPI backend (`app/api/`) and React + Vite + React Flow frontend (`app/web/`).

## How to run

From the repo root:

```bash
# API (port 8000)
python3 -m venv .venv
.venv/bin/pip install -r app/requirements.txt -r pipeline/requirements.txt
.venv/bin/uvicorn app.api.main:app --reload --port 8000

# Frontend (port 5173, proxies /api to :8000)
cd app/web && npm install && npm run dev
```

The API reads `analysis/graphs.json` (generate with `python -m analysis.build`) and
`data/stories.json`, and re-reads either file when it changes on disk. Override the
paths with `SCAM_GRAPHS_PATH` / `SCAM_STORIES_PATH`. If the graphs file is missing,
endpoints that need it return 503.

`POST /api/match` calls `pipeline.matcher.match_with_source` in a worker thread with a
timeout (`SCAM_MATCH_TIMEOUT`, default 20 s), so a slow model call never hangs the UI.
With `ANTHROPIC_API_KEY` set (environment or `.env`) it asks Sonnet; without one the
matcher uses its offline rule-based fallback. The two demo messages in `app/demo/` have
stored answers in `app/api/demo_cache.json`, used when the live model is unavailable,
times out, or only the rules answered. Set `SCAM_DEMO_CACHE_FIRST=1` to always serve
them from the cache (no network at all).

Response = matcher fields (`contract/matcher.schema.json`) plus:

| Field | Meaning |
|---|---|
| `current_step` | last of `steps_so_far`, or `null` |
| `loss_rate_if_continues` | loss rate of `likely_next_step` in that scam type's graph, or the `all` graph if the type has no such node |
| `context_graph` | which graph the numbers come from (`<scam_type>`, `all` or `null`) |
| `next_step_story_count` | stories in that graph that reach `likely_next_step` |
| `next_step_count` | transitions `current_step -> likely_next_step` in that graph |
| `current_step_out_count` | all transitions out of `current_step` in that graph |
| `source` | `live` (Sonnet just now), `cache` (stored answer: matcher disk cache, matcher demo responses or `demo_cache.json`), `fallback` (offline rule matcher), `mock` (matcher not importable) |
| `source_detail` | finer reason, e.g. `matcher_api`, `matcher_rules`, `demo_cache`, `rules_after_timeout` |

## Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/scam-types` | `[{scam_type, n_stories, loss_rate}]` |
| GET | `/api/graph/{scam_type}` | one graph entry (`all` for the combined graph), 404 if unknown |
| GET | `/api/stories?scam_type=&step=` | `[{id, scam_type, outcome, amount_lost_usd, snippet, steps}]` |
| GET | `/api/clusters` | `graphs.clusters` or `null` |
| POST | `/api/match` `{text}` | matcher result + `loss_rate_if_continues` + `source` |

## Tests

```bash
.venv/bin/python -m pytest app/api/tests
cd app/web && npm run build
```
