import json
import os
import shutil
import threading
import time
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from app.api import main

HERE = Path(__file__).parent


@pytest.fixture()
def client(tmp_path, monkeypatch):
    graphs = tmp_path / "graphs.json"
    stories = tmp_path / "stories.json"
    shutil.copy(HERE / "fixture_graphs.json", graphs)
    shutil.copy(HERE / "fixture_stories.json", stories)
    monkeypatch.setenv("SCAM_GRAPHS_PATH", str(graphs))
    monkeypatch.setenv("SCAM_STORIES_PATH", str(stories))
    from app.api.main import app

    return TestClient(app)


def test_scam_types(client):
    r = client.get("/api/scam-types")
    assert r.status_code == 200
    assert r.json() == [
        {"scam_type": "pig_butchering", "n_stories": 2, "loss_rate": 0.5},
        {"scam_type": "fake_job", "n_stories": 1, "loss_rate": None},
    ]


def test_graph_by_type_and_all(client):
    r = client.get("/api/graph/pig_butchering")
    assert r.status_code == 200
    assert r.json()["pinch_points"][0]["step"] == "small_payment"
    assert client.get("/api/graph/all").json()["n_stories"] == 3


def test_graph_unknown_404(client):
    assert client.get("/api/graph/nope").status_code == 404


def test_stories_filters(client):
    assert len(client.get("/api/stories").json()) == 3
    pb = client.get("/api/stories", params={"scam_type": "pig_butchering"}).json()
    assert {s["id"] for s in pb} == {"t-001", "t-002"}
    sp = client.get("/api/stories", params={"scam_type": "pig_butchering", "step": "small_payment"}).json()
    assert [s["id"] for s in sp] == ["t-001"]
    assert sp[0]["steps"] == ["initial_contact", "fake_proof_shown", "small_payment", "money_lost"]
    assert set(sp[0]) == {"id", "scam_type", "outcome", "amount_lost_usd", "snippet", "steps"}
    all_ = client.get("/api/stories", params={"scam_type": "all", "step": "initial_contact"}).json()
    assert len(all_) == 3


def test_clusters_null(client):
    r = client.get("/api/clusters")
    assert r.status_code == 200
    assert r.json() is None


def test_clusters_by_type(client, monkeypatch, tmp_path):
    src = json.loads(open(os.environ["SCAM_GRAPHS_PATH"]).read())
    src["clusters"] = {"clusters": [{"id": "c0"}]}
    src["clusters_by_type"] = {"fake_job": {"clusters": [{"id": "fj0"}]}}
    path = tmp_path / "graphs.json"
    path.write_text(json.dumps(src))
    monkeypatch.setenv("SCAM_GRAPHS_PATH", str(path))
    assert client.get("/api/clusters?scam_type=fake_job").json()["clusters"][0]["id"] == "fj0"
    assert client.get("/api/clusters?scam_type=romance").json()["clusters"][0]["id"] == "c0"
    assert client.get("/api/clusters?scam_type=all").json()["clusters"][0]["id"] == "c0"


def test_missing_graphs_503(client, monkeypatch, tmp_path):
    monkeypatch.setenv("SCAM_GRAPHS_PATH", str(tmp_path / "missing.json"))
    r = client.get("/api/scam-types")
    assert r.status_code == 503
    assert "not found" in r.json()["detail"]


def test_reload_on_mtime_change(client):
    path = Path(os.environ["SCAM_GRAPHS_PATH"])
    assert len(client.get("/api/scam-types").json()) == 2
    data = json.loads(path.read_text())
    data["scam_types"] = ["pig_butchering"]
    path.write_text(json.dumps(data))
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 5))
    assert len(client.get("/api/scam-types").json()) == 1


VOCAB = json.loads((HERE.parents[2] / "contract" / "vocab.json").read_text())
SCHEMA = json.loads((HERE.parents[2] / "contract" / "matcher.schema.json").read_text())
SOURCES = {"live", "cache", "fallback", "mock"}
EXTRA = {
    "loss_rate_if_continues", "current_step", "context_graph", "next_step_count",
    "current_step_out_count", "next_step_story_count", "source", "source_detail",
}
DEMO_DIR = HERE.parents[1] / "demo"

PB = {
    "scam_type": "pig_butchering",
    "confidence": 0.9,
    "steps_so_far": ["initial_contact", "fake_proof_shown"],
    "likely_next_step": "small_payment",
    "advice": "Do not send money.",
}


def _check_match(body):
    assert set(body) == set(SCHEMA["required"]) | EXTRA
    jsonschema.validate({k: body[k] for k in SCHEMA["required"]}, SCHEMA)
    assert body["scam_type"] in VOCAB["scam_types"]
    assert all(s in VOCAB["steps"] for s in body["steps_so_far"])
    assert body["source"] in SOURCES
    steps = body["steps_so_far"]
    assert body["current_step"] == (steps[-1] if steps else None)


def fake_matcher(monkeypatch, fn, rules=None):
    monkeypatch.setattr(main, "_import_matcher", lambda: (fn, rules))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Default: matcher behaves as if offline (rules only). Never touches the network."""
    fake_matcher(monkeypatch, lambda text: (dict(PB), "rules"), lambda text: dict(PB))
    monkeypatch.delenv("SCAM_DEMO_CACHE_FIRST", raising=False)


@pytest.mark.parametrize("raw,source", [("api", "live"), ("cache", "cache"), ("demo", "cache"), ("rules", "fallback")])
def test_match_source_mapping(client, monkeypatch, raw, source):
    fake_matcher(monkeypatch, lambda text: (dict(PB), raw))
    body = client.post("/api/match", json={"text": "some crypto text"}).json()
    _check_match(body)
    assert body["source"] == source
    assert body["source_detail"] == f"matcher_{raw}"


def test_match_graph_context(client, monkeypatch):
    fake_matcher(monkeypatch, lambda text: (dict(PB), "api"))
    body = client.post("/api/match", json={"text": "x"}).json()
    assert body["current_step"] == "fake_proof_shown"
    assert body["loss_rate_if_continues"] == 1.0
    assert body["context_graph"] == "pig_butchering"
    assert body["next_step_count"] == 1  # fake_proof_shown -> small_payment
    assert body["current_step_out_count"] == 2  # -> small_payment, -> victim_exits
    assert body["next_step_story_count"] == 1


def test_match_falls_back_to_all_graph(client, monkeypatch):
    r = {**PB, "scam_type": "romance", "steps_so_far": [], "likely_next_step": "initial_contact"}
    fake_matcher(monkeypatch, lambda text: (dict(r), "api"))
    body = client.post("/api/match", json={"text": "x"}).json()
    _check_match(body)
    assert body["current_step"] is None
    assert body["context_graph"] == "all"
    assert body["loss_rate_if_continues"] == 0.5
    assert body["next_step_count"] is None


def test_match_next_step_missing_everywhere(client, monkeypatch):
    r = {**PB, "scam_type": "fake_job", "likely_next_step": "large_payment"}
    fake_matcher(monkeypatch, lambda text: (dict(r), "api"))
    body = client.post("/api/match", json={"text": "x"}).json()
    assert body["loss_rate_if_continues"] is None
    assert body["context_graph"] is None


def test_match_timeout_falls_back_to_rules(client, monkeypatch):
    monkeypatch.setenv("SCAM_MATCH_TIMEOUT", "0.2")
    gate = threading.Event()

    def slow(text):
        gate.wait(5)
        return dict(PB), "api"

    rules = {**PB, "scam_type": "other", "confidence": 0.1}
    fake_matcher(monkeypatch, slow, lambda text: dict(rules))
    t0 = time.monotonic()
    body = client.post("/api/match", json={"text": "x"}).json()
    gate.set()
    assert time.monotonic() - t0 < 2
    _check_match(body)
    assert body["source"] == "fallback"
    assert body["source_detail"] == "rules_after_timeout"
    assert body["scam_type"] == "other"


def test_match_error_falls_back(client, monkeypatch):
    def boom(text):
        raise RuntimeError("no network")

    fake_matcher(monkeypatch, boom, lambda text: dict(PB))
    body = client.post("/api/match", json={"text": "x"}).json()
    assert body["source"] == "fallback"
    assert body["source_detail"] == "rules_after_error"


def test_match_mock_when_matcher_unavailable(client, monkeypatch):
    fake_matcher(monkeypatch, None, None)
    body = client.post("/api/match", json={"text": "I can teach you crypto investing"}).json()
    _check_match(body)
    assert body["source"] == "mock"
    assert body["scam_type"] == "pig_butchering"
    job = client.post("/api/match", json={"text": "remote job, simple tasks"}).json()
    assert job["scam_type"] == "fake_job"
    assert job["loss_rate_if_continues"] is None  # small_payment absent from fake_job and all fixture graphs


def test_demo_cache_entries_valid():
    entries = json.loads((HERE.parent / "demo_cache.json").read_text())
    assert set(entries) == {"crypto_mentor", "fake_job"}
    for entry in entries.values():
        assert (DEMO_DIR / entry["file"]).exists()
        jsonschema.validate(entry["response"], SCHEMA)


@pytest.mark.parametrize("name,scam_type", [("crypto_mentor", "pig_butchering"), ("fake_job", "fake_job")])
def test_demo_messages_offline_use_cache(client, name, scam_type):
    text = (DEMO_DIR / f"{name}.txt").read_text()
    body = client.post("/api/match", json={"text": text}).json()
    _check_match(body)
    assert body["source"] == "cache"
    assert body["source_detail"] == "demo_cache"
    assert body["scam_type"] == scam_type
    assert body["current_step"] == "urgency_pressure"
    assert body["likely_next_step"] == "small_payment"


def test_demo_message_whitespace_insensitive(client):
    text = (DEMO_DIR / "fake_job.txt").read_text()
    body = client.post("/api/match", json={"text": "  " + text.replace("\n\n", "\n") + "\n"}).json()
    assert body["source_detail"] == "demo_cache"


def test_demo_message_live_answer_wins(client, monkeypatch):
    fake_matcher(monkeypatch, lambda text: (dict(PB), "api"))
    text = (DEMO_DIR / "crypto_mentor.txt").read_text()
    body = client.post("/api/match", json={"text": text}).json()
    assert body["source"] == "live"
    assert body["steps_so_far"] == PB["steps_so_far"]


def test_demo_cache_first_env(client, monkeypatch):
    monkeypatch.setenv("SCAM_DEMO_CACHE_FIRST", "1")

    def never(text):
        raise AssertionError("matcher should not be called")

    fake_matcher(monkeypatch, never)
    text = (DEMO_DIR / "crypto_mentor.txt").read_text()
    assert client.post("/api/match", json={"text": text}).json()["source"] == "cache"


def test_demo_message_on_timeout_uses_cache(client, monkeypatch):
    monkeypatch.setenv("SCAM_MATCH_TIMEOUT", "0.2")
    gate = threading.Event()
    fake_matcher(monkeypatch, lambda text: (gate.wait(5), (dict(PB), "api"))[1])
    text = (DEMO_DIR / "fake_job.txt").read_text()
    body = client.post("/api/match", json={"text": text}).json()
    gate.set()
    assert body["source"] == "cache"
    assert body["scam_type"] == "fake_job"


def test_real_rule_matcher_offline(client, monkeypatch):
    """The real pipeline rule matcher (no network) produces a schema-valid response."""
    from pipeline.matcher import rule_match

    fake_matcher(monkeypatch, lambda text: (rule_match(text), "rules"))
    body = client.post("/api/match", json={"text": "Hi, wrong number! I'm a crypto mentor, move to WhatsApp"}).json()
    _check_match(body)
    assert body["source"] == "fallback"


@pytest.mark.parametrize("text", ["", "   \n "])
def test_match_empty_text_422(client, text):
    assert client.post("/api/match", json={"text": text}).status_code == 422
