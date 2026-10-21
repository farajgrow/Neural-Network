"""Scam Scripts API.

Run from the repo root:
    uvicorn app.api.main:app --reload --port 8000

Data files are re-read whenever their mtime changes, so swapping in a new
data/stories.json or analysis/graphs.json takes effect without a restart.
Override paths with SCAM_GRAPHS_PATH and SCAM_STORIES_PATH.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

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
def clusters(scam_type: str | None = None) -> dict | None:
    """Cross-type clusters, or one type's variants when `scam_type` is given.

    Falls back to the cross-type view for "all" or a type without its own clustering.
    """
    graphs = load_graphs()
    if scam_type and scam_type != "all":
        by_type = graphs.get("clusters_by_type") or {}
        if scam_type in by_type:
            return by_type[scam_type]
    return graphs.get("clusters")


# ---- Matcher ---------------------------------------------------------------
#
# POST /api/match runs pipeline.matcher in a worker thread with a timeout, so a
# slow or hung model call never blocks the UI. `source` in the response says
# where the answer came from (the matcher tries Jev, then Sonnet, then rules):
#   jev       Jev (TypeSafe System One) answered just now; needs TYPESAFE_API_KEY
#   live      Sonnet answered just now (Jev unavailable); needs ANTHROPIC_API_KEY
#   cache     a stored answer: the matcher's disk cache, its stored demo
#             responses, or app/api/demo_cache.json for the app/demo messages
#   fallback  the matcher's offline rule-based matcher (no key, API failure,
#             or the model call timed out)
#   mock      pipeline.matcher could not be imported or crashed; canned answer
#
# Jev normally answers in under a second. Its client allows 15 s plus one retry
# before the matcher tries Sonnet, so SCAM_MATCH_TIMEOUT (default 20 s) can cut
# off a slow Jev + Sonnet chain; the demo cache, then the rules, answer instead.

log = logging.getLogger("app.api.match")

DEMO_DIR = REPO_ROOT / "app" / "demo"
DEMO_CACHE_PATH = Path(__file__).resolve().parent / "demo_cache.json"

_MATCHER_SOURCES = {"jev": "jev", "api": "live", "cache": "cache", "demo": "cache", "rules": "fallback"}

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="matcher")


def match_timeout() -> float:
    try:
        return float(os.environ.get("SCAM_MATCH_TIMEOUT", "20"))
    except ValueError:
        return 20.0


def _demo_cache_first() -> bool:
    return os.environ.get("SCAM_DEMO_CACHE_FIRST", "").lower() in ("1", "true", "yes")


class MatchRequest(BaseModel):
    text: str = Field(..., min_length=1)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text is blank")
        return v


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _load_demo_cache() -> dict[str, dict]:
    """Normalised demo text -> stored response, from demo_cache.json + app/demo/*.txt."""
    try:
        entries = json.loads(DEMO_CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("demo cache unavailable: %s", exc)
        return {}
    out = {}
    for name, entry in entries.items():
        try:
            text = (DEMO_DIR / entry["file"]).read_text()
        except (OSError, KeyError):
            continue
        out[_normalise(text)] = entry["response"]
    return out


def demo_cached(text: str) -> dict | None:
    hit = _load_demo_cache().get(_normalise(text))
    return dict(hit) if hit else None


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


def _import_matcher():
    """Returns (match_with_source, rule_match); either may be None."""
    try:
        from pipeline import matcher  # type: ignore
    except Exception as exc:
        log.warning("pipeline.matcher not importable: %s", type(exc).__name__)
        return None, None
    mws = getattr(matcher, "match_with_source", None)
    if mws is None and hasattr(matcher, "match"):
        mws = lambda text: (matcher.match(text), "api")  # noqa: E731
    return mws, getattr(matcher, "rule_match", None)


def _run_matcher(text: str) -> tuple[dict, str, str]:
    """Returns (result, source, source_detail)."""
    demo = demo_cached(text)
    if demo and _demo_cache_first():
        return demo, "cache", "demo_cache"

    match_with_source, rule_match = _import_matcher()
    if match_with_source is None:
        return (demo, "cache", "demo_cache") if demo else (_mock_match(text), "mock", "matcher_unavailable")

    detail = "error"
    try:
        result, raw = _executor.submit(match_with_source, text).result(timeout=match_timeout())
    except FutureTimeout:
        log.warning("matcher timed out after %.0fs, falling back", match_timeout())
        detail = "timeout"
    except Exception as exc:
        log.warning("matcher failed, falling back: %s", type(exc).__name__)
    else:
        source = _MATCHER_SOURCES.get(raw, "live")
        # For the two demo messages, the stored answer beats the keyword rules.
        if source == "fallback" and demo:
            return demo, "cache", "demo_cache"
        return dict(result), source, f"matcher_{raw}"

    if demo:
        return demo, "cache", "demo_cache"
    if rule_match is not None:
        try:
            return rule_match(text), "fallback", f"rules_after_{detail}"
        except Exception as exc:
            log.warning("rule matcher failed: %s", type(exc).__name__)
    return _mock_match(text), "mock", f"mock_after_{detail}"


def _graph_context(scam_type: str, current: str | None, nxt: str | None) -> dict:
    """Loss rate of the next step plus transition counts, from the type's graph
    (or the combined "all" graph if the type has no node for the next step)."""
    out = {
        "loss_rate_if_continues": None,
        "context_graph": None,
        "next_step_count": None,
        "current_step_out_count": None,
        "next_step_story_count": None,
    }
    if not nxt:
        return out
    try:
        graphs = load_graphs().get("graphs", {})
    except HTTPException:
        return out
    for name in (scam_type, "all"):
        g = graphs.get(name) or {}
        node = next((n for n in g.get("nodes", []) if n.get("step") == nxt), None)
        if node is None:
            continue
        out["loss_rate_if_continues"] = node.get("loss_rate")
        out["context_graph"] = name
        out["next_step_story_count"] = node.get("count")
        if current:
            edges = [e for e in g.get("edges", []) if e.get("from") == current]
            out["current_step_out_count"] = sum(e.get("count", 0) for e in edges)
            out["next_step_count"] = sum(e.get("count", 0) for e in edges if e.get("to") == nxt)
        return out
    return out


# ---- Our model (analysis/model, GRU v1) ----------------------------------------
#
# A small GRU trained on data/stories.json predicts the next step and the chance of
# losing money from the steps so far. It needs torch (analysis/requirements.txt);
# without it, or when no script step was seen, `model` is null and nothing else changes.

MODEL_CARD_PATH = REPO_ROOT / "analysis" / "model" / "model_card.json"


def _model_eval() -> dict | None:
    """Cross-validated accuracy from the model card, so the UI can show how good the model is."""
    try:
        cv = json.loads(MODEL_CARD_PATH.read_text())["cv_metrics"]
        return {
            "next_top1": round(cv["gru_next_step"]["top1"]["mean"], 3),
            "loss_auc": round(cv["gru_loss_all_prefixes"]["auc"]["mean"], 3),
            "folds": cv.get("folds"),
        }
    except (OSError, KeyError, TypeError, ValueError):
        return None


def _model_prediction(steps: list[str], scam_type: str) -> dict | None:
    """Run the GRU on the matched steps; None if torch, the weights or a script step is missing."""
    try:
        from analysis.model.predict import predict
    except Exception as exc:  # torch not installed, or the model package failed to import
        log.info("model: unavailable (%s)", type(exc).__name__)
        return None
    try:
        out = predict(list(steps), scam_type)
    except ValueError:
        return None
    except Exception as exc:
        log.warning("model: prediction failed: %s", type(exc).__name__)
        return None
    ranked = sorted(out["next_step_probs"].items(), key=lambda kv: kv[1], reverse=True)
    return {
        "name": "gru-v1",
        "top_next": out["top_next"],
        "next_steps": [{"step": s, "p": round(p, 4)} for s, p in ranked[:3]],
        "p_loss": round(out["p_loss"], 4),
        "eval": _model_eval(),
    }


@app.post("/api/match")
def match_endpoint(req: MatchRequest) -> dict:
    result, source, detail = _run_matcher(req.text)
    steps = result.get("steps_so_far") or []
    current = steps[-1] if steps else None
    result["current_step"] = current
    result.update(_graph_context(result.get("scam_type", ""), current, result.get("likely_next_step")))
    result["source"] = source
    result["source_detail"] = detail
    result["model"] = _model_prediction(steps, result.get("scam_type", ""))
    return result
