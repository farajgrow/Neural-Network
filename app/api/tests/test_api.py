import json
import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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


def _check_match(body):
    assert body["scam_type"] in VOCAB["scam_types"]
    assert 0 <= body["confidence"] <= 1
    assert all(s in VOCAB["steps"] for s in body["steps_so_far"])
    assert body["likely_next_step"] in VOCAB["steps"]
    assert body["advice"]
    assert body["source"] in ("live", "mock")
    assert "loss_rate_if_continues" in body


def test_match_mock_crypto(client):
    r = client.post("/api/match", json={"text": "I can teach you crypto investing, guaranteed profits"})
    assert r.status_code == 200
    body = r.json()
    _check_match(body)
    if body["source"] == "mock":
        assert body["scam_type"] == "pig_butchering"
        assert body["likely_next_step"] == "small_payment"
        assert body["loss_rate_if_continues"] == 1.0


def test_match_mock_job(client):
    body = client.post("/api/match", json={"text": "Hi, I'm a recruiter with a remote job, simple tasks"}).json()
    _check_match(body)
    if body["source"] == "mock":
        assert body["scam_type"] == "fake_job"
        assert body["loss_rate_if_continues"] is None  # small_payment absent from fake_job fixture graph


def test_match_empty_text_422(client):
    assert client.post("/api/match", json={"text": ""}).status_code == 422
