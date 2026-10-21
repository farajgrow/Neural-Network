"""Tests for pipeline.spotcheck. Reviewer input is scripted."""

from __future__ import annotations

import json

from pipeline import spotcheck


def make_story(story_id, scam_type="fake_job"):
    return {
        "id": story_id,
        "source": "reddit",
        "scam_type": scam_type,
        "events": [{"step": "initial_contact", "actor": "scammer", "detail": "Texted about a job"}],
        "outcome": "escaped",
        "amount_lost_usd": None,
        "snippet": "A recruiter text that turned into a check scam.",
    }


def make_post(post_id):
    return {"id": post_id, "source": "reddit", "text": f"Story for {post_id}.", "n_chars": 20}


def scripted(*answers):
    it = iter(answers)
    return lambda prompt: next(it)


STORIES = [make_story(f"reddit-{i}") for i in range(10)] + [make_story("mock-001")]
POSTS = {p["id"]: p for p in (make_post(f"reddit-{i}") for i in range(10))}


# --------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------


def test_sample_skips_stories_without_a_post():
    sample = spotcheck.pick_sample(STORIES, POSTS, n=50, seed=0)
    assert len(sample) == 10
    assert "mock-001" not in {s["id"] for s in sample}


def test_sample_is_reproducible_and_ignores_input_order():
    a = spotcheck.pick_sample(STORIES, POSTS, n=4, seed=3)
    b = spotcheck.pick_sample(list(reversed(STORIES)), POSTS, n=4, seed=3)
    assert [s["id"] for s in a] == [s["id"] for s in b]
    assert len(a) == 4


# --------------------------------------------------------------------------
# Reviewing
# --------------------------------------------------------------------------


def test_review_story_records_each_field_and_note():
    result = spotcheck.review_story(
        STORIES[0], POSTS["reddit-0"], ask=scripted("y", "n", "?", "Y", " missed a step "),
        show=lambda _: None,
    )
    assert result == {
        "id": "reddit-0",
        "checks": {"scam_type": True, "events": False, "outcome": None, "amount_lost_usd": True},
        "note": "missed a step",
    }


def test_review_story_reasks_on_bad_answer():
    result = spotcheck.review_story(
        STORIES[0], POSTS["reddit-0"], ask=scripted("maybe", "y", "y", "y", "y", ""),
        show=lambda _: None,
    )
    assert all(result["checks"].values())


def test_review_saves_each_story_and_resumes_after_quit(tmp_path):
    results = tmp_path / "results.jsonl"
    sample = spotcheck.pick_sample(STORIES, POSTS, n=3, seed=0)

    done = spotcheck.review(sample, POSTS, results, ask=scripted("y", "y", "y", "y", "", "q"),
                            show=lambda _: None)
    assert done == 1
    assert list(spotcheck.load_results(results)) == [sample[0]["id"]]

    done = spotcheck.review(sample, POSTS, results,
                            ask=scripted(*["n", "n", "n", "n", ""] * 2), show=lambda _: None)
    assert done == 2
    assert set(spotcheck.load_results(results)) == {s["id"] for s in sample}


def test_review_stops_cleanly_on_eof(tmp_path):
    def eof(prompt):
        raise EOFError

    sample = spotcheck.pick_sample(STORIES, POSTS, n=2, seed=0)
    assert spotcheck.review(sample, POSTS, tmp_path / "r.jsonl", ask=eof, show=lambda _: None) == 0


def test_later_result_for_same_id_wins(tmp_path):
    path = tmp_path / "results.jsonl"
    spotcheck.append_result(path, {"id": "a", "checks": {"scam_type": False}, "note": ""})
    spotcheck.append_result(path, {"id": "a", "checks": {"scam_type": True}, "note": ""})
    assert spotcheck.load_results(path)["a"]["checks"]["scam_type"] is True


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def test_accuracy_counts_fields_and_skips_unsure():
    results = [
        {"id": "a", "checks": {"scam_type": True, "events": True, "outcome": True,
                               "amount_lost_usd": None}},
        {"id": "b", "checks": {"scam_type": True, "events": False, "outcome": True,
                               "amount_lost_usd": True}},
    ]
    counts = spotcheck.accuracy(results)
    assert counts["scam_type"] == (2, 2)
    assert counts["events"] == (1, 2)
    assert counts["amount_lost_usd"] == (1, 1)
    assert counts["all_fields"] == (1, 2)
    assert counts["overall"] == (6, 7)


def test_report_cli(tmp_path, capsys):
    path = tmp_path / "results.jsonl"
    path.write_text(json.dumps({"id": "a", "checks": dict.fromkeys(spotcheck.FIELDS, True),
                                "note": ""}) + "\n")
    assert spotcheck.main(["--report", "--results", str(path)]) == 0
    out = capsys.readouterr().out
    assert "spot-check of 1 stories" in out
    assert "100.0%  (4/4)" in out


def test_report_cli_with_no_results(tmp_path):
    assert spotcheck.main(["--report", "--results", str(tmp_path / "none.jsonl")]) == 1
