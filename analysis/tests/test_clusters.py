import json

import pytest

from analysis.build import DEFAULT_STORIES
from analysis.clusters import (
    adjusted_rand_index,
    build_clusters,
    edit_distance,
    normalised_edit_distance,
    purity,
    sequence_distance,
)
from analysis.graphs import load_stories, load_vocab

VOCAB = load_vocab()

PIG = ["initial_contact", "trust_building", "platform_switch", "fake_proof_shown", "small_payment", "large_payment"]
JOB = ["initial_contact", "authority_claim", "urgency_pressure", "remote_access_granted", "credentials_shared"]


def story(sid, steps, outcome="lost_money", scam_type="pig_butchering"):
    return {"id": sid, "scam_type": scam_type, "outcome": outcome,
            "events": [{"step": s, "actor": "scammer", "detail": "x"} for s in steps]}


def group(prefix, steps, n, scam_type, outcome="lost_money"):
    return [story(f"{prefix}{i}", steps, outcome, scam_type) for i in range(n)]


@pytest.mark.parametrize("a, b, expected", [
    ([], [], 0),
    (["a"], [], 1),
    (["a", "b", "c"], ["a", "b", "c"], 0),
    (["a", "b", "c"], ["a", "c"], 1),
    (["a", "b"], ["b", "a"], 2),
    (list("kitten"), list("sitting"), 3),
])
def test_edit_distance(a, b, expected):
    assert edit_distance(a, b) == expected
    assert edit_distance(b, a) == expected


def test_normalised_and_combined_distance_bounds():
    assert normalised_edit_distance([], []) == 0.0
    assert normalised_edit_distance(["a", "b"], ["c", "d"]) == 1.0
    assert sequence_distance(PIG, PIG) == 0.0
    assert sequence_distance(PIG, JOB) == pytest.approx(sequence_distance(JOB, PIG))
    assert 0.0 < sequence_distance(PIG, JOB) <= 1.0


def test_identical_sequences_cluster_together():
    stories = group("p", PIG, 4, "pig_butchering") + group("j", JOB, 4, "fake_job")
    result = build_clusters(stories, VOCAB)
    a = result["assignments"]
    assert len({a[f"p{i}"] for i in range(4)}) == 1
    assert len({a[f"j{i}"] for i in range(4)}) == 1


def test_different_groups_separate_and_match_labels():
    pig_variant = ["initial_contact", "platform_switch", "trust_building", "fake_proof_shown", "small_payment"]
    stories = (group("p", PIG, 5, "pig_butchering")
               + group("q", pig_variant, 5, "pig_butchering", "escaped")
               + group("j", JOB, 6, "fake_job", "escaped"))
    result = build_clusters(stories, VOCAB)
    a = result["assignments"]
    assert a["j0"] != a["p0"]
    assert result["agreement"]["purity"] == 1.0
    by_id = {c["id"]: c for c in result["clusters"]}
    assert by_id[a["j0"]]["scam_type_counts"] == {"fake_job": 6}
    assert by_id[a["j0"]]["typical_sequence"] == JOB
    assert by_id[a["j0"]]["loss_rate"] == 0.0
    assert by_id[a["j0"]]["label"].startswith("fake_job · ")
    sizes = [c["size"] for c in result["clusters"]]
    assert sizes == sorted(sizes, reverse=True)


def test_outcome_steps_ignored_for_clustering():
    stories = [story("a", PIG + ["money_lost"]), story("b", PIG + ["victim_exits"], "escaped")]
    result = build_clusters(stories, VOCAB)
    assert result["assignments"]["a"] == result["assignments"]["b"]


def test_deterministic_and_order_independent():
    stories = (group("p", PIG, 3, "pig_butchering") + group("j", JOB, 3, "fake_job")
               + [story("x", ["initial_contact", "small_payment"], "unknown", "other")])
    first = build_clusters(stories, VOCAB)
    second = build_clusters(list(reversed(stories)), VOCAB)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


@pytest.mark.parametrize("stories", [
    [],
    [story("a", PIG)],
    [story("a", []), story("b", [])],
    [{"id": "bad", "events": "junk"}, {"events": []}, story("a", PIG), story("a", JOB)],
])
def test_degenerate_input_does_not_crash(stories):
    result = build_clusters(stories, VOCAB)
    assert set(result) >= {"method", "assignments", "clusters", "agreement"}
    assert sum(c["size"] for c in result["clusters"]) == len(result["assignments"])


def test_agreement_metrics():
    assert adjusted_rand_index([0, 0, 1, 1], ["a", "a", "b", "b"]) == 1.0
    assert adjusted_rand_index([0, 0, 0], ["a", "a", "a"]) == 1.0
    assert adjusted_rand_index([0], ["a"]) is None
    assert adjusted_rand_index([0, 1, 0, 1], ["a", "a", "b", "b"]) < 0.0
    assert purity([0, 0, 1, 1], ["a", "b", "b", "b"]) == 0.75


def test_smoke_mock_stories():
    stories = load_stories(DEFAULT_STORIES)
    result = build_clusters(stories, VOCAB)
    assert len(result["assignments"]) == len({s["id"] for s in stories})
    assert [c["id"] for c in result["clusters"]] == [f"c{i}" for i in range(len(result["clusters"]))]
