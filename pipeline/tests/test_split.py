"""Tests for pipeline.split."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pipeline import split
from pipeline.split import (
    assign,
    containment,
    group_ids,
    shingles,
    split as split_stories,
)


# --------------------------------------------------------------------------
# Shingles and Containment
# --------------------------------------------------------------------------


def test_shingles_short_and_empty():
    assert shingles("") == set()
    assert shingles("   \n\t  ") == set()
    assert shingles("hello") == {("hello",)}
    assert shingles("crypto scam") == {("crypto", "scam")}
    assert shingles("one two three four") == {("one", "two", "three", "four")}
    assert shingles("one two three four five") == {("one", "two", "three", "four", "five")}

    # Case insensitivity, punctuation stripping and contraction handling
    punctuated = "Don't! Pay, 'them' 123 USD."
    assert shingles(punctuated) == {("don't", "pay", "'them'", "123", "usd")}

    # 6 words produces 2 consecutive 5-word tuples
    six_words = shingles("one two three four five six")
    assert six_words == {
        ("one", "two", "three", "four", "five"),
        ("two", "three", "four", "five", "six"),
    }


def test_containment():
    sh_a = {("one", "two", "three", "four", "five")}
    sh_b = {("six", "seven", "eight", "nine", "ten")}

    # Identical sets
    assert containment(sh_a, sh_a) == 1.0

    # Disjoint sets
    assert containment(sh_a, sh_b) == 0.0

    # Subset: containment is 1.0 when one set is completely contained in the other
    sh_superset = sh_a | sh_b | {("a", "b", "c", "d", "e")}
    assert containment(sh_a, sh_superset) == 1.0
    assert containment(sh_superset, sh_a) == 1.0

    # Partial overlap
    s1 = {("a",), ("b",), ("c",)}
    s2 = {("b",), ("c",), ("d",), ("e",)}
    # intersection has 2 elements, min size is 3 -> 2/3
    assert containment(s1, s2) == pytest.approx(2 / 3)

    # Empty sets
    assert containment(set(), sh_a) == 0.0
    assert containment(sh_a, set()) == 0.0
    assert containment(set(), set()) == 0.0


# --------------------------------------------------------------------------
# Near-duplicate and Transitive Grouping
# --------------------------------------------------------------------------


def test_near_duplicate_posts_grouped_and_unrelated_isolated():
    post_a = (
        "I was contacted on WhatsApp by someone claiming to be an investment advisor. "
        "She walked me through registering an account on a crypto exchange and sending USDT. "
        "I deposited $500 initially and saw fake profits before being asked for a withdrawal fee."
    )
    # post_b is post_a plus additional sentences (a repost or expansion)
    post_b = (
        post_a + " They threatened me with legal consequences if I did not wire another $1000. "
        "I realized it was a scam and lost all my money."
    )
    # post_c is a completely unrelated scam scenario
    post_c = (
        "A contractor offered to repair my roof following severe storm damage in our county. "
        "He demanded half the payment up front in cash and never showed up to start the work."
    )

    texts = {"post-a": post_a, "post-b": post_b, "post-c": post_c}
    groups = group_ids(texts, threshold=0.3)

    # Find the group containing post-a
    a_groups = [g for g in groups if "post-a" in g]
    assert len(a_groups) == 1
    group_a = a_groups[0]
    assert "post-b" in group_a
    assert "post-c" not in group_a

    # post-c is in its own singleton group
    c_groups = [g for g in groups if "post-c" in g]
    assert len(c_groups) == 1
    assert c_groups[0] == ["post-c"]


def test_transitive_grouping():
    # Construct A, B, C such that:
    # A shares sentences with B (containment >= 0.3)
    # B shares sentences with C (containment >= 0.3)
    # A and C share no words (containment == 0.0)
    w_a = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
    w_b = "kilo lima mike november oscar papa quebec romeo sierra tango"
    w_c = "uniform victor whiskey xray yankee zulu zero one two three"

    text_a = f"{w_a} {w_b}"
    text_b = f"{w_a} {w_b} {w_c}"
    text_c = f"{w_b} {w_c}"

    sh_a = shingles(text_a)
    sh_b = shingles(text_b)
    sh_c = shingles(text_c)

    # Verify containment relationships
    assert containment(sh_a, sh_b) >= 0.3
    assert containment(sh_b, sh_c) >= 0.3

    # Now make text_a and text_c truly disjoint while B connects them:
    # A has w_a + w_b, B has w_b + w_c, but wait: if A has w_b and C has w_b they share w_b!
    # Let's construct 4 distinct blocks:
    # Block 1 (w1): words 1-10
    # Block 2 (w2): words 11-20
    # Block 3 (w3): words 21-30
    # Block 4 (w4): words 31-40
    # Let A = w1 + w2 (20 words)
    # Let B = w2 + w3 (20 words)
    # Let C = w3 + w4 (20 words)
    # Shingles of A: 5-word tuples from w1 + w2. Contains all shingles within w2 (6 shingles).
    # Shingles of B: 5-word tuples from w2 + w3. Contains all shingles within w2 (6 shingles) and within w3 (6 shingles).
    # Shingles of C: 5-word tuples from w3 + w4. Contains all shingles within w3 (6 shingles).
    # Overlap(A, B) = at least 6 shingles. min(|A|, |B|) = 16. Containment = 6 / 16 = 0.375 >= 0.3!
    # Overlap(B, C) = at least 6 shingles. min(|B|, |C|) = 16. Containment = 6 / 16 = 0.375 >= 0.3!
    # Overlap(A, C) = 0 words shared! Containment = 0.0 < 0.3!

    b1 = "wordaa wordab wordac wordad wordae wordaf wordag wordah wordai wordaj"
    b2 = "wordba wordbb wordbc wordbd wordbe wordbf wordbg wordbh wordbi wordbj"
    b3 = "wordca wordcb wordcc wordcd wordce wordcf wordcg wordch wordci wordcj"
    b4 = "wordda worddb worddc worddd wordde worddf worddg worddh worddi worddj"

    ta = f"{b1} {b2}"
    tb = f"{b2} {b3}"
    tc = f"{b3} {b4}"

    sa = shingles(ta)
    sb = shingles(tb)
    sc = shingles(tc)

    assert containment(sa, sb) >= 0.3
    assert containment(sb, sc) >= 0.3
    assert containment(sa, sc) == 0.0

    groups = group_ids({"A": ta, "B": tb, "C": tc}, threshold=0.3)
    assert len(groups) == 1
    assert groups[0] == ["A", "B", "C"]


# --------------------------------------------------------------------------
# Group Assignment and SHA-256 Rule
# --------------------------------------------------------------------------


def test_group_lands_on_one_side_matching_sha256():
    group1 = ["story-01", "story-02", "story-03"]
    group2 = ["story-10", "story-20"]
    group3 = ["story-99"]

    assignment = assign([group1, group2, group3])

    for group in [group1, group2, group3]:
        min_id = min(group)
        digest = hashlib.sha256(min_id.encode("utf-8")).hexdigest()
        is_test = int(digest, 16) % 10 < 2
        target = "test" if is_test else "train"
        other = "train" if is_test else "test"

        for item in group:
            assert item in assignment[target]
            assert item not in assignment[other]


def test_assign_both_train_and_test_branches():
    # Find IDs that deterministically hit test (< 2) and train (>= 2)
    test_id = None
    train_id = None
    for i in range(100):
        candidate = f"candidate-{i:03d}"
        mod = int(hashlib.sha256(candidate.encode("utf-8")).hexdigest(), 16) % 10
        if mod < 2 and test_id is None:
            test_id = candidate
        elif mod >= 2 and train_id is None:
            train_id = candidate
        if test_id and train_id:
            break

    assert test_id is not None
    assert train_id is not None

    assignment = assign([[test_id, f"{test_id}-sibling"], [train_id, f"{train_id}-sibling"]])
    assert test_id in assignment["test"]
    assert f"{test_id}-sibling" in assignment["test"]
    assert train_id in assignment["train"]
    assert f"{train_id}-sibling" in assignment["train"]


# --------------------------------------------------------------------------
# Determinism and Split Proportions
# --------------------------------------------------------------------------


def test_deterministic():
    stories = [
        {"id": f"story-{i}", "snippet": f"Brief summary for story {i} involving cash scam"}
        for i in range(20)
    ]
    posts = [
        {"id": f"story-{i}", "text": f"Full detailed narrative for story {i} involving wire fraud"}
        for i in range(15)
    ]

    res1 = split_stories(stories, posts)
    res2 = split_stories(stories, posts)
    assert res1 == res2

    # Reversing input order must yield identical sorted output
    res3 = split_stories(list(reversed(stories)), list(reversed(posts)))
    assert res1 == res3


def test_roughly_20_percent_test_on_synthetic_posts():
    stories = [
        {
            "id": f"syn-{i:04d}",
            "snippet": f"Distinct synthetic post scenario number {i} with completely unique tokens {i}",
        }
        for i in range(200)
    ]
    res = split_stories(stories, [])
    test_count = len(res["test"])
    train_count = len(res["train"])

    assert train_count + test_count == 200
    assert set(res["train"]).isdisjoint(set(res["test"]))

    test_pct = test_count / 200.0
    assert 0.10 <= test_pct <= 0.30


# --------------------------------------------------------------------------
# Text Resolution: Post text vs Snippet fallback
# --------------------------------------------------------------------------


def test_text_resolution_prefers_post_over_snippet():
    # s1 has clean post; s2 has clean post identical to s1's clean post (near dupes)
    # s3 has no clean post, but snippet is unrelated
    stories = [
        {"id": "s1", "snippet": "Unrelated snippet for story 1"},
        {"id": "s2", "snippet": "Unrelated snippet for story 2"},
        {"id": "s3", "snippet": "Snippet for story 3 about lottery prize checks"},
    ]
    shared_text = "I received a phone call from an alleged federal agent saying my identity was stolen."
    posts = [
        {"id": "s1", "text": shared_text},
        {"id": "s2", "text": shared_text + " He demanded gift cards immediately."},
    ]

    res = split_stories(stories, posts)
    # s1 and s2 should group together based on post text (not snippet)
    assert res["groups"] == [["s1", "s2"]]


# --------------------------------------------------------------------------
# Main CLI End to End
# --------------------------------------------------------------------------


def test_main_end_to_end(tmp_path, capsys):
    stories_data = [
        {"id": f"story-{i}", "snippet": f"Snippet summary for story {i}"}
        for i in range(10)
    ]
    shared_text = (
        "Victim was targeted through an Instagram direct message promoting forex trading profits. "
        "Transferred crypto via CashApp and was locked out of account."
    )
    posts_data = [
        {"id": "story-0", "text": shared_text},
        {"id": "story-1", "text": shared_text + " They also requested bank account routing numbers."},
        {"id": "story-2", "text": "Unrelated clean post narrative about a task scam liking videos."},
        {"id": "story-3", "text": "Another unrelated post about puppy purchase deposit fraud."},
    ]
    # stories 4..9 have no clean posts, so they use snippet

    stories_path = tmp_path / "stories.json"
    posts_path = tmp_path / "clean" / "posts.jsonl"
    out_path = tmp_path / "split.json"

    stories_path.write_text(json.dumps(stories_data, indent=2), encoding="utf-8")
    posts_path.parent.mkdir(parents=True, exist_ok=True)
    with posts_path.open("w", encoding="utf-8") as f:
        for p in posts_data:
            f.write(json.dumps(p) + "\n")

    split.main([
        "--stories", str(stories_path),
        "--posts", str(posts_path),
        "--output", str(out_path),
    ])

    assert out_path.exists()
    raw_content = out_path.read_text(encoding="utf-8")
    assert raw_content.endswith("\n")

    result = json.loads(raw_content)
    assert set(result.keys()) == {"train", "test", "groups"}

    all_ids = {f"story-{i}" for i in range(10)}
    train_set = set(result["train"])
    test_set = set(result["test"])

    # train ∪ test == all story ids and no overlap
    assert train_set | test_set == all_ids
    assert train_set & test_set == set()

    # Sorted lists
    assert result["train"] == sorted(result["train"])
    assert result["test"] == sorted(result["test"])

    # story-0 and story-1 should be in groups
    assert [["story-0", "story-1"]] == result["groups"]

    # Only groups with 2+ members
    assert all(len(g) >= 2 for g in result["groups"])

    # Summary stdout
    captured = capsys.readouterr().out
    assert "stories: 10" in captured
    assert "groups with 2+ members: 1" in captured
    assert f"train: {len(result['train'])}" in captured
    assert f"test: {len(result['test'])}" in captured
