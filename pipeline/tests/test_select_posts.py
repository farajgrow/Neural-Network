import json

from pipeline import extract, select_posts

STORY = (
    "Lost $2,000 to a fake trader\n\n"
    "I met him on a dating app. He told me he traded crypto and showed me his profits. "
    "I signed up on his platform and deposited $500. It showed I earned money. "
    "Then I sent $1,500 more. When I tried to withdraw they asked for a fee. I realized it was a scam."
)
OTHER_STORY = (
    "Fake recruiter on Telegram\n\n"
    "A recruiter messaged me on Telegram about remote work. I completed tasks rating hotels and "
    "they paid me $30 at first. Later they said my account went negative and I needed to deposit "
    "$800. I paid it, then they wanted more. My brother warned me and I stopped and reported them."
)
QUESTION = (
    "Is this a scam?\n\n"
    "Someone texted me about a job. Is this legit? Should I answer? What do they want? Has anyone seen this?"
)


def test_relevance_keeps_first_person_story():
    assert select_posts.relevance_reasons(STORY) == []


def test_relevance_drops_questions_without_events():
    reasons = select_posts.relevance_reasons(QUESTION)
    assert "no_narrative" in reasons and "question_only" in reasons


def test_relevance_off_topic_needs_a_scam_word():
    story = STORY.replace("fake ", "").replace("scam", "mistake")
    assert select_posts.relevance_reasons(story) == []
    assert select_posts.relevance_reasons(story, off_topic=True) == ["no_scam_word"]


def test_near_duplicates_match_reposts_only():
    seen = select_posts.NearDuplicates()
    seen.add("a", select_posts.shingles(STORY))
    assert seen.match(select_posts.shingles(STORY + " Update: still no reply.")) == "a"
    assert seen.match(select_posts.shingles(QUESTION)) is None


def post(post_id, text=STORY):
    return {"id": post_id, "source": "reddit", "text": text, "n_chars": len(text)}


def test_select_applies_quota_dedupe_and_exclude():
    clean = [
        post("t-a1"),
        post("t-a2", STORY + " Again."),  # near-duplicate of t-a1
        post("t-b1", OTHER_STORY),
        post("t-b2", QUESTION),
        post("t-x"),
    ]
    subs = {"t-a1": "A", "t-a2": "A", "t-b1": "B", "t-b2": "B", "t-x": "A"}
    selected, stats = select_posts.select(clean, subs, {"A": 5, "B": 5}, {"t-x"}, total=10)
    ids = {p["id"] for p in selected}
    assert len(ids & {"t-a1", "t-a2"}) == 1
    assert "t-b2" not in ids and "t-x" not in ids
    assert stats["excluded"] == 1 and stats["near_duplicate"] == 1
    assert all(p["subreddit"] == subs[p["id"]] for p in selected)


def test_select_stops_at_quota():
    clean = [post(f"t-{i}", f"{STORY} Story number {i}. " * (i + 1)) for i in range(4)]
    selected, stats = select_posts.select(clean, {p["id"]: "A" for p in clean}, {"A": 1}, set(), total=10)
    assert len(selected) == 1 and stats["over_quota"] + stats["near_duplicate"] == 3


def test_import_replies_writes_valid_and_rejects_invalid(tmp_path):
    good = {
        "id": "t-1",
        "scam_type": "pig_butchering",
        "events": [{"step": "initial_contact", "actor": "scammer", "detail": "met on dating app"}],
        "outcome": "unknown",
        "amount_lost_usd": None,
        "snippet": "A dating match pushed a fake trading platform.",
    }
    bad = {**good, "id": "t-2", "scam_type": "not_a_type"}
    replies = tmp_path / "replies.jsonl"
    replies.write_text("\n".join(json.dumps(r) for r in (good, bad, {**good, "id": "t-missing"})))

    written, rejected = extract.import_replies(replies, [post("t-1"), post("t-2")], tmp_path / "cache")

    assert written == 1
    assert [r[0] for r in rejected] == ["t-2", "t-missing"]
    cached = json.loads(extract.cache_path("t-1", tmp_path / "cache").read_text())
    assert "id" not in cached and cached["scam_type"] == "pig_butchering"
