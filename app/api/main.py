"""Scam Scripts API.

Run from the repo root:
    uvicorn app.api.main:app --reload --port 8000

Data files are re-read whenever their mtime changes, so swapping in a new
data/stories.json or analysis/graphs.json takes effect without a restart.
Override paths with SCAM_GRAPHS_PATH and SCAM_STORIES_PATH.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]


def graphs_path() -> Path:
    return Path(os.environ.get("SCAM_GRAPHS_PATH", REPO_ROOT / "analysis" / "graphs.json"))


def stories_path() -> Path:
    return Path(os.environ.get("SCAM_STORIES_PATH", REPO_ROOT / "data" / "stories.json"))


class _JsonCache:
    """Caches parsed JSON per path, reloading when the file's mtime changes."""

    def __init__(self) -> None:
        self._entries: dict[Path, tuple[float, Any]] = {}

    def load(self, path: Path, missing_detail: str) -> Any:
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            raise HTTPException(status_code=503, detail=missing_detail)
        cached = self._entries.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            # Likely caught mid-write; serve the previous copy if we have one.
            if cached:
                return cached[1]
            raise HTTPException(status_code=503, detail=f"{path.name} is not valid JSON: {exc}")
        self._entries[path] = (mtime, data)
        return data


_cache = _JsonCache()


def load_graphs() -> dict:
    p = graphs_path()
    return _cache.load(
        p, f"Graphs file not found at {p}. Run `python -m analysis.build` to generate it."
    )


def load_stories() -> list[dict]:
    p = stories_path()
    return _cache.load(p, f"Stories file not found at {p}.")


app = FastAPI(title="Scam Scripts API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/scam-types")
def scam_types() -> list[dict]:
    g = load_graphs()
    graphs = g.get("graphs", {})
    out = []
    for t in g.get("scam_types", []):
        entry = graphs.get(t, {})
        out.append(
            {"scam_type": t, "n_stories": entry.get("n_stories", 0), "loss_rate": entry.get("loss_rate")}
        )
    return out


@app.get("/api/graph/{scam_type}")
def graph(scam_type: str) -> dict:
    graphs = load_graphs().get("graphs", {})
    if scam_type not in graphs:
        raise HTTPException(status_code=404, detail=f"Unknown scam type: {scam_type}")
    return graphs[scam_type]


def _story_summary(s: dict) -> dict:
    return {
        "id": s.get("id"),
        "scam_type": s.get("scam_type"),
        "outcome": s.get("outcome"),
        "amount_lost_usd": s.get("amount_lost_usd"),
        "snippet": s.get("snippet"),
        "steps": [e.get("step") for e in s.get("events", [])],
    }


@app.get("/api/stories")
def stories(
    scam_type: str | None = Query(None, description="Filter by scam type ('all' or empty for every type)"),
    step: str | None = Query(None, description="Only stories whose events include this step"),
) -> list[dict]:
    out = []
    for s in load_stories():
        if scam_type and scam_type != "all" and s.get("scam_type") != scam_type:
            continue
        if step and not any(e.get("step") == step for e in s.get("events", [])):
            continue
        out.append(_story_summary(s))
    return out


@app.get("/api/clusters")
def clusters() -> dict | None:
    return load_graphs().get("clusters")


# ---- Matcher ---------------------------------------------------------------


class MatchRequest(BaseModel):
    text: str = Field(..., min_length=1)


_MOCK_PIG_BUTCHERING = {
    "scam_type": "pig_butchering",
    "confidence": 0.82,
    "steps_so_far": ["initial_contact", "trust_building", "fake_proof_shown"],
    "likely_next_step": "small_payment",
    "advice": "This matches a crypto investment scam. Do not send any money, even a small test amount, and stop replying.",
}

_MOCK_FAKE_JOB = {
    "scam_type": "fake_job",
    "confidence": 0.78,
    "steps_so_far": ["initial_contact", "platform_switch", "trust_building"],
    "likely_next_step": "small_payment",
    "advice": "Real employers never ask you to pay or deposit money to unlock tasks or wages. Stop and verify the company through its official website.",
}


def _mock_match(text: str) -> dict:
    t = text.lower()
    if any(k in t for k in ("crypto", "invest", "trading", "bitcoin", "usdt")):
        return dict(_MOCK_PIG_BUTCHERING)
    if any(k in t for k in ("job", "task", "recruit", "hiring", "salary", "commission")):
        return dict(_MOCK_FAKE_JOB)
    return dict(_MOCK_PIG_BUTCHERING)


def _run_matcher(text: str) -> tuple[dict, str]:
    try:
        from pipeline.matcher import match  # type: ignore
    except Exception:
        return _mock_match(text), "mock"
    try:
        return match(text), "live"
    except Exception as exc:  # live matcher failed (e.g. no API key); keep the demo alive
        print(f"pipeline.matcher.match failed, using mock: {exc!r}")
        return _mock_match(text), "mock"


def _node_loss_rate(scam_type: str, step: str | None) -> float | None:
    if not step:
        return None
    try:
        graphs = load_graphs().get("graphs", {})
    except HTTPException:
        return None
    for node in graphs.get(scam_type, {}).get("nodes", []):
        if node.get("step") == step:
            return node.get("loss_rate")
    return None


@app.post("/api/match")
def match_endpoint(req: MatchRequest) -> dict:
    result, source = _run_matcher(req.text)
    result = dict(result)
    result["loss_rate_if_continues"] = _node_loss_rate(result.get("scam_type", ""), result.get("likely_next_step"))
    result["source"] = source
    return result
