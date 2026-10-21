import pytest

from analysis.build import DEFAULT_STORIES
from analysis.graphs import build_graph, build_graphs_file, load_stories, load_vocab, step_sequence

VOCAB = load_vocab()


def story(sid, steps, outcome, scam_type="pig_butchering"):
    return {
        "id": sid,
        "source": "reddit",
        "scam_type": scam_type,
        "events": [{"step": s, "actor": "scammer", "detail": "x"} for s in steps],
        "outcome": outcome,
        "amount_lost_usd": None,
        "snippet": "x",
    }


def nodes_by_step(graph):
    return {n["step"]: n for n in graph["nodes"]}


def edge_counts(graph):
    return {(e["from"], e["to"]): e["count"] for e in graph["edges"]}


def test_loss_rate_ignores_unknown_outcomes():
    stories = [
        story("a", ["initial_contact"], "lost_money"),
        story("b", ["initial_contact"], "escaped"),
        story("c", ["initial_contact"], "unknown"),
        story("d", ["initial_contact"], "unknown"),
    ]
    graph = build_graph(stories, "t", VOCAB)
    assert graph["loss_rate"] == 0.5
    assert nodes_by_step(graph)["initial_contact"]["loss_rate"] == 0.5
    assert nodes_by_step(graph)["initial_contact"]["count"] == 4
    assert graph["outcome_counts"] == {"lost_money": 1, "escaped": 1, "unknown": 2}


def test_loss_rate_null_when_all_unknown():
    graph = build_graph([story("a", ["initial_contact"], "unknown")], "t", VOCAB)
    assert graph["loss_rate"] is None
    assert graph["nodes"][0]["loss_rate"] is None


def test_consecutive_duplicates_collapse():
    steps = ["initial_contact", "trust_building", "trust_building", "small_payment", "small_payment"]
    assert step_sequence(story("a", steps, "lost_money"), frozenset(VOCAB["steps"])) == [
        "initial_contact", "trust_building", "small_payment",
    ]
    graph = build_graph([story("a", steps, "lost_money")], "t", VOCAB)
    assert ("trust_building", "trust_building") not in edge_counts(graph)
    assert nodes_by_step(graph)["small_payment"]["avg_position"] == 1.0


def test_repeated_step_counts_once_per_story():
    steps = ["initial_contact", "trust_building", "small_payment", "trust_building"]
    graph = build_graph([story("a", steps, "lost_money")], "t", VOCAB)
    assert nodes_by_step(graph)["trust_building"]["count"] == 1
    assert graph["step_to_story_ids"]["trust_building"] == ["a"]


def test_invalid_steps_and_fields_are_skipped():
    bad = story("a", ["initial_contact", "made_up_step", "small_payment"], "weird_outcome")
    bad["events"].append("not an event")
    bad["events"].append({"actor": "victim"})
    graph = build_graph([bad, {"id": "b", "events": None}], "t", VOCAB)
    assert {n["step"] for n in graph["nodes"]} == {"initial_contact", "small_payment"}
    assert edge_counts(graph) == {("initial_contact", "small_payment"): 1}
    assert graph["outcome_counts"]["unknown"] == 2


def test_edge_counts():
    stories = [
        story("a", ["initial_contact", "platform_switch", "small_payment"], "lost_money"),
        story("b", ["initial_contact", "platform_switch", "victim_exits"], "escaped"),
        story("c", ["initial_contact", "small_payment"], "lost_money"),
    ]
    assert edge_counts(build_graph(stories, "t", VOCAB)) == {
        ("initial_contact", "platform_switch"): 2,
        ("platform_switch", "small_payment"): 1,
        ("platform_switch", "victim_exits"): 1,
        ("initial_contact", "small_payment"): 1,
    }


def test_pinch_point_jump_uses_edge_weighted_predecessors():
    stories = [
        story("a", ["initial_contact", "small_payment", "money_lost"], "lost_money"),
        story("b", ["initial_contact", "small_payment", "money_lost"], "lost_money"),
        story("c", ["initial_contact", "victim_exits"], "escaped"),
        story("d", ["initial_contact", "trust_building", "small_payment"], "lost_money"),
    ]
    pinch = {p["step"]: p for p in build_graph(stories, "t", VOCAB)["pinch_points"]}
    assert pinch["trust_building"]["prev_loss_rate"] == 0.75
    assert pinch["trust_building"]["jump"] == 0.25
    # small_payment: preds initial_contact (0.75, x2) and trust_building (1.0, x1)
    assert pinch["small_payment"]["prev_loss_rate"] == pytest.approx(0.8333, abs=1e-4)
    assert pinch["small_payment"]["jump"] == pytest.approx(0.1667, abs=1e-4)
    assert [p["step"] for p in build_graph(stories, "t", VOCAB)["pinch_points"]] == [
        "trust_building", "small_payment",
    ]


def test_outcome_steps_never_pinch_points():
    stories = [
        story("a", ["initial_contact", "money_lost"], "lost_money"),
        story("b", ["initial_contact", "victim_exits"], "escaped"),
    ]
    assert build_graph(stories, "t", VOCAB)["pinch_points"] == []


def test_pinch_points_require_positive_jump():
    stories = [story(str(i), ["initial_contact", "trust_building"], "lost_money") for i in range(3)]
    assert build_graph(stories, "t", VOCAB)["pinch_points"] == []


def test_escape_point_ranking():
    stories = [
        story("a", ["initial_contact", "victim_suspicious", "victim_exits"], "escaped"),
        story("b", ["initial_contact", "victim_suspicious", "victim_exits"], "escaped"),
        story("c", ["initial_contact", "victim_suspicious", "small_payment"], "lost_money"),
        story("d", ["initial_contact", "third_party_intervened", "victim_exits"], "escaped"),
        story("e", ["initial_contact", "victim_exits"], "escaped"),
        story("f", ["initial_contact", "platform_switch", "victim_exits"], "escaped"),
        story("g", ["initial_contact", "platform_switch", "small_payment"], "lost_money"),
    ]
    escapes = build_graph(stories, "t", VOCAB)["escape_points"]
    # ties on exit_count=1 break by exit_rate: 1.0 > 0.5 > 1/7
    assert [p["step"] for p in escapes] == ["victim_suspicious", "third_party_intervened", "platform_switch"]
    assert escapes[0] == {"step": "victim_suspicious", "exit_count": 2, "exit_rate": pytest.approx(0.6667, abs=1e-4)}
    assert escapes[1]["exit_rate"] == 1.0 and escapes[2]["exit_rate"] == 0.5
    assert all(p["step"] != "victim_exits" for p in escapes)


def test_graphs_file_groups_by_type():
    stories = [
        story("a", ["initial_contact"], "lost_money", "fake_job"),
        story("b", ["initial_contact"], "lost_money", "fake_job"),
        story("c", ["initial_contact"], "escaped", "romance"),
        story("d", ["initial_contact"], "escaped", "not_a_type"),
    ]
    out = build_graphs_file(stories, VOCAB)
    assert out["scam_types"] == ["fake_job", "romance", "other"]
    assert set(out["graphs"]) == {"fake_job", "romance", "other", "all"}
    assert out["graphs"]["all"]["n_stories"] == 4
    assert out["clusters"] is None


def test_smoke_mock_stories():
    stories = load_stories(DEFAULT_STORIES)
    out = build_graphs_file(stories, VOCAB)
    assert out["n_stories"] == len(stories)
    assert sum(out["graphs"][t]["n_stories"] for t in out["scam_types"]) == len(stories)
    for graph in out["graphs"].values():
        steps = {n["step"] for n in graph["nodes"]}
        assert set(graph["step_to_story_ids"]) == steps
        for e in graph["edges"]:
            assert e["from"] in steps and e["to"] in steps
        for n in graph["nodes"]:
            assert 0.0 <= n["avg_position"] <= 1.0
            assert n["count"] == len(graph["step_to_story_ids"][n["step"]])
        assert len(graph["pinch_points"]) <= 3 and len(graph["escape_points"]) <= 3
        assert all(p["step"] not in {"money_lost", "victim_exits"} for p in graph["pinch_points"])
        assert all(p["jump"] > 0 for p in graph["pinch_points"])
