# app

FastAPI backend (`app/api/`) and React + Vite + React Flow frontend (`app/web/`).

## How to run

From the repo root:

```bash
# API (port 8000)
python3 -m venv .venv
.venv/bin/pip install -r app/requirements.txt
.venv/bin/uvicorn app.api.main:app --reload --port 8000

# Frontend (port 5173, proxies /api to :8000)
cd app/web && npm install && npm run dev
```

The API reads `analysis/graphs.json` (generate with `python -m analysis.build`) and
`data/stories.json`, and re-reads either file when it changes on disk. Override the
paths with `SCAM_GRAPHS_PATH` / `SCAM_STORIES_PATH`. If the graphs file is missing,
endpoints that need it return 503.

`POST /api/match` uses `pipeline.matcher.match` when it is importable and falls back
to a canned response otherwise (`"source": "mock"` in the response).

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
