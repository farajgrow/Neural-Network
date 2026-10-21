"""Tests for pipeline.cfpb_bulk."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from pipeline import cfpb_bulk
from pipeline.cfpb_bulk import (
    BUCKETS,
    BoundedHeap,
    classify_bucket,
    is_excluded_product,
    is_scam_complaint,
    make_cfpb_post,
    parse_date_received_utc,
    process_cfpb_bulk,
    sha1_key,
)

BASE_NARRATIVE = (
    "I was contacted by someone online who convinced me that we were in a serious relationship. "
    "After several weeks of communicating every single day, they told me that they needed money "
    "urgently for an unexpected medical emergency. I sent the funds via bank wire and later "
    "discovered that all the photos and identity documents they provided were completely fabricated. "
    "I lost a significant amount of money and had to report this incident to law enforcement."
)
assert len(BASE_NARRATIVE) >= 400


def make_zip(zip_path: Path, rows: list[dict[str, str]], filename: str = "complaints.csv") -> Path:
    """Create a test zip file containing complaints CSV rows."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "Date received",
        "Product",
        "Sub-product",
        "Issue",
        "Sub-issue",
        "Consumer complaint narrative",
        "Complaint ID",
    ]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        zf.writestr(filename, buf.getvalue().encode("utf-8"))
    return zip_path


def make_row(
    complaint_id: str = "1001",
    date_received: str = "2024-06-15",
    product: str = "Money transfer, virtual currency, or money service",
    sub_product: str = "",
    issue: str = "Fraud or scam",
    sub_issue: str = "",
    narrative: str = BASE_NARRATIVE,
) -> dict[str, str]:
    """Helper to create a CSV row dictionary with sensible defaults."""
    return {
        "Date received": date_received,
        "Product": product,
        "Sub-product": sub_product,
        "Issue": issue,
        "Sub-issue": sub_issue,
        "Consumer complaint narrative": narrative,
        "Complaint ID": complaint_id,
    }


# --------------------------------------------------------------------------
# Date and Hash Key Utilities
# --------------------------------------------------------------------------


def test_parse_date_received_utc():
    # 2024-01-01 00:00:00 UTC == 1704067200
    assert parse_date_received_utc("2024-01-01") == 1704067200
    assert parse_date_received_utc("2024-01-01T00:00:00") == 1704067200
    assert parse_date_received_utc("invalid-date") == 0
    assert parse_date_received_utc("") == 0


def test_sha1_key_deterministic():
    cid = "98765"
    expected = hashlib.sha1(b"cfpb-bulk-v198765").hexdigest()
    assert sha1_key(cid) == expected


# --------------------------------------------------------------------------
# Filter 1: Short narrative
# --------------------------------------------------------------------------


def test_filter_short_narrative_dropped(tmp_path):
    short_text = "Too short " * 10  # 100 chars < 400
    assert len(short_text.strip()) < 400

    row_short = make_row(complaint_id="1", narrative=short_text)
    row_ok = make_row(complaint_id="2", narrative="A" * 400)

    zip_file = make_zip(tmp_path / "test.zip", [row_short, row_ok])
    posts_file = tmp_path / "posts.jsonl"

    res = process_cfpb_bulk(zip_file, posts_file)
    assert res["total_rows"] == 2
    assert res["drop_counts"]["short_narrative"] == 1
    assert res["posts_appended"] == 1


# --------------------------------------------------------------------------
# Filter 2: Excluded Products
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "product",
    [
        "Debt collection",
        "Credit reporting, credit repair services, or other personal consumer reports",
        "Conventional home mortgage",
        "Federal student loan servicing",
        "Credit repair services",
    ],
)
def test_filter_excluded_products(product, tmp_path):
    assert is_excluded_product(product)

    row = make_row(complaint_id="10", product=product, issue="Fraud or scam")
    zip_file = make_zip(tmp_path / f"test_{abs(hash(product))}.zip", [row])
    posts_file = tmp_path / "posts.jsonl"

    res = process_cfpb_bulk(zip_file, posts_file)
    assert res["drop_counts"]["excluded_product"] == 1
    assert res["posts_appended"] == 0


# --------------------------------------------------------------------------
# Filter 3: Primary and Secondary Scam Criteria
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "product",
    [
        "Money transfer, virtual currency, or money service",
        "Money transfers",
        "Prepaid card",
    ],
)
def test_filter_primary_keep(product, tmp_path):
    assert is_scam_complaint(product, "Fraud or scam", BASE_NARRATIVE)

    row = make_row(complaint_id="20", product=product, issue="Fraud or scam")
    zip_file = make_zip(tmp_path / f"primary_{abs(hash(product))}.zip", [row])
    posts_file = tmp_path / "posts.jsonl"

    res = process_cfpb_bulk(zip_file, posts_file)
    assert res["drop_counts"]["not_scam"] == 0
    assert res["posts_appended"] == 1


def test_filter_primary_wrong_issue_dropped(tmp_path):
    # Primary product but issue is not "Fraud or scam" and no scam words
    text = "Problem with account transfer fees and customer service response delay." * 8
    assert len(text) >= 400
    row = make_row(
        complaint_id="21",
        product="Money transfers",
        issue="Other transaction problem",
        narrative=text,
    )
    zip_file = make_zip(tmp_path / "primary_wrong_issue.zip", [row])
    posts_file = tmp_path / "posts.jsonl"

    res = process_cfpb_bulk(zip_file, posts_file)
    assert res["drop_counts"]["not_scam"] == 1
    assert res["posts_appended"] == 0


@pytest.mark.parametrize(
    ("product", "scam_word"),
    [
        ("Checking or savings account", "scammer"),
        ("Checking or savings account", "scammers"),
        ("Bank account or service", "scammed"),
        ("Bank account or service", "fraudster"),
        ("Credit card or prepaid card", "fell victim"),
    ],
)
def test_filter_secondary_keep_with_regex_word(product, scam_word, tmp_path):
    narrative = f"I {scam_word} when an unauthorized transaction appeared on my statement. " + ("word " * 80)
    assert len(narrative) >= 400
    assert is_scam_complaint(product, "Managing an account", narrative)

    row = make_row(
        complaint_id="30",
        product=product,
        issue="Managing an account",
        narrative=narrative,
    )
    zip_file = make_zip(tmp_path / f"secondary_{abs(hash(scam_word))}.zip", [row])
    posts_file = tmp_path / "posts.jsonl"

    res = process_cfpb_bulk(zip_file, posts_file)
    assert res["drop_counts"]["not_scam"] == 0
    assert res["posts_appended"] == 1


def test_non_scam_bank_complaint_dropped(tmp_path):
    # Bank account complaint with no scam words
    narrative = (
        "The bank charged multiple excessive overdraft fees on my checking account despite "
        "my deposits being made in a timely manner according to their published schedule. "
        "I called customer service multiple times and spoke to representatives who refused "
        "to reverse the charges or explain why the hold was placed on my funds. "
        "I request a full refund of all overdraft fees charged during this billing cycle. "
        + ("Additional account details and dispute description. " * 5)
    )
    assert len(narrative) >= 400
    assert not is_scam_complaint("Checking or savings account", "Managing an account", narrative)

    row = make_row(
        complaint_id="31",
        product="Checking or savings account",
        issue="Managing an account",
        narrative=narrative,
    )
    zip_file = make_zip(tmp_path / "bank_non_scam.zip", [row])
    posts_file = tmp_path / "posts.jsonl"

    res = process_cfpb_bulk(zip_file, posts_file)
    assert res["drop_counts"]["not_scam"] == 1
    assert res["posts_appended"] == 0


# --------------------------------------------------------------------------
# Filter 4: Deduplication by ID
# --------------------------------------------------------------------------


def test_filter_duplicate_complaint_id(tmp_path):
    row1 = make_row(complaint_id="dup-1")
    row2 = make_row(complaint_id="dup-1")  # repeated inside zip

    zip_file = make_zip(tmp_path / "dup.zip", [row1, row2])
    posts_file = tmp_path / "posts.jsonl"

    res = process_cfpb_bulk(zip_file, posts_file)
    assert res["total_rows"] == 2
    assert res["drop_counts"]["duplicate_id"] == 1
    assert res["posts_appended"] == 1


# --------------------------------------------------------------------------
# Bucketing and Precedence
# --------------------------------------------------------------------------


def test_bucket_pig_butchering_requires_both_groups():
    # Only crypto term, no action term -> not pig butchering
    t_crypto = "I bought crypto with my savings and lost everything. " + ("padding " * 50)
    assert classify_bucket(t_crypto) != "pig_butchering"

    # Only action term, no crypto term -> not pig butchering
    t_action = "I deposited money into a fake trading platform and was scammed. " + ("padding " * 50)
    assert classify_bucket(t_action) != "pig_butchering"

    # Both present -> pig butchering
    t_both1 = "I bought bitcoin on a trading platform that turned out to be fake. " + ("padding " * 50)
    assert classify_bucket(t_both1) == "pig_butchering"

    t_both2 = "I transferred usdt and then could not withdraw my funds. " + ("padding " * 50)
    assert classify_bucket(t_both2) == "pig_butchering"


@pytest.mark.parametrize(
    ("phrase", "expected_bucket"),
    [
        ("I matched on a dating app with someone", "romance"),
        ("I fell in love with a person online", "romance"),
        ("My boyfriend asked me for money", "romance"),
        ("I received a job offer from a fake company", "fake_job"),
        ("A recruiter reached out to me via text", "fake_job"),
        ("I applied for a remote job that was a scam", "fake_job"),
        ("Someone called pretending to be my bank", "impersonation"),
        ("They called posing as federal agents", "impersonation"),
        ("An impersonator stole my credentials", "impersonation"),
        ("They impersonated an officer from the department", "impersonation"),
        ("The caller spoofed my bank phone number", "impersonation"),
        ("I received a call from the fraud department", "impersonation"),
        ("A letter arrived claiming to be from the IRS", "impersonation"),
        ("They said my SSA number was suspended", "impersonation"),
        ("They asked for remote access to my computer", "impersonation"),
        ("They instructed me to install AnyDesk on my phone", "impersonation"),
        ("I bought an item on facebook marketplace", "marketplace"),
        ("The seller never shipped the merchandise", "marketplace"),
        ("I paid a deposit for a puppy that did not exist", "marketplace"),
        ("I purchased concert tickets that were invalid", "marketplace"),
        ("I put down a security deposit for a fake rental apartment", "marketplace"),
    ],
)
def test_each_bucket_keywords(phrase, expected_bucket):
    narrative = f"{phrase}. " + ("padding text " * 40)
    assert classify_bucket(narrative) == expected_bucket


def test_bucket_other():
    narrative = (
        "I was scammed by an unknown person who took my money through a fraudulent scheme. "
        + ("padding " * 50)
    )
    assert classify_bucket(narrative) == "other"


def test_bucket_first_match_order():
    # pig_butchering beats romance
    t1 = "I bought crypto on a trading platform after someone said they fell in love with me."
    assert classify_bucket(t1) == "pig_butchering"

    # romance beats fake_job
    t2 = "My girlfriend sent me a job offer for a position abroad."
    assert classify_bucket(t2) == "romance"

    # fake_job beats impersonation
    t3 = "A recruiter from the fraud department contacted me."
    assert classify_bucket(t3) == "fake_job"

    # impersonation beats marketplace
    t4 = "The caller from the fraud department told me to send money to a seller."
    assert classify_bucket(t4) == "impersonation"

    # marketplace beats other
    t5 = "A seller on marketplace scammed me out of my payment."
    assert classify_bucket(t5) == "marketplace"


# --------------------------------------------------------------------------
# Sampling and Determinism
# --------------------------------------------------------------------------


def test_bounded_heap_ordering_and_cap():
    heap = BoundedHeap(capacity=2)
    # Generate 5 complaint IDs
    ids = [f"id-{i}" for i in range(5)]
    for cid in ids:
        heap.push(cid, {"Complaint ID": cid})

    assert len(heap) == 2
    sorted_rows = heap.get_sorted_rows()
    assert len(sorted_rows) == 2

    # Check that the 2 kept rows match the lowest SHA-1 hashes
    all_sorted_ids = sorted(ids, key=sha1_key)
    expected_ids = all_sorted_ids[:2]
    actual_ids = [r["Complaint ID"] for r in sorted_rows]
    assert actual_ids == expected_ids


def test_per_bucket_cap_and_determinism(tmp_path):
    # Create 6 romance complaints
    rows = [
        make_row(
            complaint_id=f"rom-{i}",
            narrative="I met someone on a dating app and fell in love with them. " + ("padding text " * 50),
        )
        for i in range(6)
    ]
    zip_file = make_zip(tmp_path / "sample.zip", rows)
    posts_file1 = tmp_path / "posts1.jsonl"
    posts_file2 = tmp_path / "posts2.jsonl"

    res1 = process_cfpb_bulk(zip_file, posts_file1, per_bucket=2)
    assert res1["kept_before_cap"]["romance"] == 6
    assert res1["kept_after_cap"]["romance"] == 2
    assert res1["posts_appended"] == 2

    # Rerun with separate output file produces identical chosen IDs
    res2 = process_cfpb_bulk(zip_file, posts_file2, per_bucket=2)
    lines1 = [json.loads(line)["source_id"] for line in posts_file1.read_text(encoding="utf-8").splitlines()]
    lines2 = [json.loads(line)["source_id"] for line in posts_file2.read_text(encoding="utf-8").splitlines()]
    assert lines1 == lines2

    # Expected IDs should match the 2 smallest sha1 hashes
    sorted_ids = sorted([r["Complaint ID"] for r in rows], key=sha1_key)[:2]
    assert lines1 == sorted_ids


# --------------------------------------------------------------------------
# Exact Post Shape
# --------------------------------------------------------------------------


def test_post_exact_shape():
    complaint_id = "778899"
    narrative = BASE_NARRATIVE
    date_received = "2024-01-01"
    bucket = "romance"

    post = make_cfpb_post(complaint_id, narrative, date_received, bucket)

    expected_keys = {
        "id",
        "source",
        "source_id",
        "title",
        "body",
        "created_utc",
        "score",
        "url",
        "fetched_via",
        "subreddit",
    }
    assert set(post.keys()) == expected_keys
    assert post["id"] == "cfpb-778899"
    assert post["source"] == "cfpb"
    assert post["source_id"] == "778899"
    assert post["title"] == ""
    assert post["body"] == BASE_NARRATIVE
    assert post["created_utc"] == 1704067200
    assert post["score"] is None
    assert post["url"] == "https://www.consumerfinance.gov/data-research/consumer-complaints/search/detail/778899"
    assert post["fetched_via"] == "cfpb:wayback-2026-08-15:romance"
    assert post["subreddit"] is None


# --------------------------------------------------------------------------
# End-to-End CLI Run
# --------------------------------------------------------------------------


def test_main_end_to_end(tmp_path, capsys):
    # Pre-populate posts.jsonl with an existing reddit post and a cfpb post
    existing_posts = [
        {
            "id": "reddit-abc",
            "source": "reddit",
            "source_id": "abc",
            "title": "reddit title",
            "body": "Existing reddit story " * 20,
            "created_utc": 1600000000,
            "score": 100,
            "url": "https://reddit.com/r/Scams/comments/abc/",
            "fetched_via": "arctic:top",
            "subreddit": "Scams",
        },
        {
            "id": "cfpb-already-here",
            "source": "cfpb",
            "source_id": "already-here",
            "title": "",
            "body": "Existing CFPB story " * 30,
            "created_utc": 1650000000,
            "score": None,
            "url": "https://www.consumerfinance.gov/data-research/consumer-complaints/search/detail/already-here",
            "fetched_via": "cfpb:wayback-2026-08-15:other",
            "subreddit": None,
        },
    ]

    posts_file = tmp_path / "posts.jsonl"
    with posts_file.open("w", encoding="utf-8") as f:
        for p in existing_posts:
            f.write(json.dumps(p) + "\n")

    # Build zip with:
    # 1. new valid complaint
    # 2. complaint with id "already-here" (duplicate of existing post)
    # 3. short complaint (dropped)
    # 4. excluded product complaint (dropped)
    rows = [
        make_row(
            complaint_id="brand-new-1",
            narrative="I met someone on a dating website and lost money. " + ("padding text " * 50),
        ),
        make_row(complaint_id="already-here"),
        make_row(complaint_id="short-1", narrative="too short"),
        make_row(complaint_id="excl-1", product="Debt collection"),
    ]
    zip_file = make_zip(tmp_path / "test_main.zip", rows)

    # Run main CLI
    cfpb_bulk.main([
        "--zip", str(zip_file),
        "--posts", str(posts_file),
        "--per-bucket", "200",
    ])

    out = capsys.readouterr().out
    assert "rows read: 4" in out
    assert "posts appended: 1" in out

    lines = [json.loads(line) for line in posts_file.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3
    # Existing posts preserved intact
    assert lines[0]["id"] == "reddit-abc"
    assert lines[1]["id"] == "cfpb-already-here"
    # New post appended
    assert lines[2]["id"] == "cfpb-brand-new-1"
    assert lines[2]["fetched_via"] == "cfpb:wayback-2026-08-15:romance"

    # Rerun appends nothing new
    cfpb_bulk.main([
        "--zip", str(zip_file),
        "--posts", str(posts_file),
        "--per-bucket", "200",
    ])
    out_rerun = capsys.readouterr().out
    assert "posts appended: 0" in out_rerun

    lines_after_rerun = posts_file.read_text(encoding="utf-8").splitlines()
    assert len(lines_after_rerun) == 3


# --------------------------------------------------------------------------
# Narrative with Commas, Quotes and Newlines Survives CSV Roundtrip
# --------------------------------------------------------------------------


def test_narrative_special_characters_csv_roundtrip(tmp_path):
    complex_narrative = (
        "First line has a comma, semicolon; and 'single quotes'.\n"
        'Second line has "double quotes", tabs\tand dashes -- and special characters like $1,250.00.\n'
        "Third line has questions? And exclamation marks! Plus parentheses (important detail).\n"
        "Fourth line explains: I fell in love with someone on a dating app and got scammed.\n"
        + ("Additional detailed narrative text with numbers 123456789 and punctuation! " * 8)
    )
    assert len(complex_narrative) >= 400

    row = make_row(complaint_id="special-chars-99", narrative=complex_narrative)
    zip_file = make_zip(tmp_path / "special.zip", [row])
    posts_file = tmp_path / "posts.jsonl"

    res = process_cfpb_bulk(zip_file, posts_file)
    assert res["posts_appended"] == 1

    saved_post = json.loads(posts_file.read_text(encoding="utf-8").strip())
    assert saved_post["body"] == complex_narrative
    assert saved_post["id"] == "cfpb-special-chars-99"
