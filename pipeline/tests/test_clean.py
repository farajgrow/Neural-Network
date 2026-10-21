import json

import pytest

from pipeline import clean
from pipeline.clean import (
    body_fingerprint,
    build_text,
    clean_posts,
    clean_text,
    is_link_or_screenshot_only,
    is_meta_post,
    is_removed_or_empty,
    redact_pii,
    strip_markdown,
)

STORY = (
    "I met someone on Hinge who said she was a crypto trader. After two weeks of chatting "
    "she moved the conversation to WhatsApp and showed me screenshots of her profits. "
    "She walked me through opening an account on Coinbase and sending USDT to a trading "
    "site. I started with $500 and the site showed a fake profit of $3,200. When I tried "
    "to withdraw they asked for a 20% tax first. I paid $1,200 more via Zelle before I "
    "realised it was a scam and stopped replying."
)


def make_post(post_id: str, body: str = STORY, title: str = "Lost $1,700 to a Hinge match", **extra) -> dict:
    return {
        "id": post_id,
        "source": "reddit",
        "source_id": post_id.removeprefix("reddit-"),
        "title": title,
        "body": body,
        "created_utc": 1700000000,
        "score": 10,
        "url": f"https://www.reddit.com/r/Scams/comments/{post_id}",
        "fetched_via": "arctic:top",
        **extra,
    }


# --------------------------------------------------------------------------
# Markdown and links
# --------------------------------------------------------------------------


def test_markdown_link_keeps_text_and_drops_url():
    assert strip_markdown("Check [this site](https://fake-exchange.com/login) now") == "Check this site now"


def test_bare_urls_are_removed():
    text = strip_markdown("They sent https://bit.ly/3xYz and www.fake-bank.com. Then nothing.")
    assert "http" not in text and "www" not in text
    assert text == "They sent and . Then nothing."


def test_markdown_formatting_is_stripped():
    raw = (
        "# My story\n"
        "## Part one\n"
        "This was **really** bad and *very* _sneaky_ and ~~fine~~.\n"
        "> quoted message from the scammer\n"
        ">> nested quote\n"
        "```\n"
        "pasted text inside a fence\n"
        "```\n"
        "- first bullet\n"
        "* second bullet\n"
        "1. numbered item\n"
        "---\n"
        "Use `inline code` and >!a spoiler!< here."
    )
    assert strip_markdown(raw) == (
        "My story\n"
        "Part one\n"
        "This was really bad and very sneaky and fine.\n"
        "quoted message from the scammer\n"
        "nested quote\n\n"
        "pasted text inside a fence\n\n"
        "first bullet\n"
        "second bullet\n"
        "numbered item\n\n"
        "Use inline code and a spoiler here."
    )


def test_snake_case_is_not_treated_as_emphasis():
    assert strip_markdown("the user_name_field was blank") == "the user_name_field was blank"


def test_html_entities_and_zero_width_spaces():
    raw = "Tom &amp; Jerry &amp;amp; friends said &quot;hi&quot; &gt; bye&#x200B;\n\n&#x200B;\n\nEnd"
    assert strip_markdown(raw) == 'Tom & Jerry & friends said "hi" > bye\n\nEnd'


def test_excess_blank_lines_and_spaces_collapse():
    assert strip_markdown("one   two\n\n\n\n\nthree  \n   four") == "one two\n\nthree\nfour"


# --------------------------------------------------------------------------
# PII
# --------------------------------------------------------------------------


def test_email_is_redacted():
    assert redact_pii("Email me at john.doe+scam@gmail.com today") == "Email me at [email] today"


@pytest.mark.parametrize(
    "phone",
    ["(555) 123-4567", "555-123-4567", "555.123.4567", "555 123 4567", "5551234567", "+1 555 123 4567"],
)
def test_us_phone_numbers_are_redacted(phone):
    assert redact_pii(f"He called from {phone} last night") == "He called from [phone] last night"


@pytest.mark.parametrize("phone", ["+44 20 7946 0958", "+91 98765 43210", "+234 803 123 4567", "+447946095812"])
def test_international_phone_numbers_are_redacted(phone):
    assert redact_pii(f"Text {phone} on WhatsApp") == "Text [phone] on WhatsApp"


def test_short_plus_number_is_not_a_phone():
    assert redact_pii("My balance showed +50000 profit") == "My balance showed +50000 profit"


def test_reddit_usernames_are_redacted():
    assert redact_pii("Thanks u/helpful_person and /u/Other-User!") == "Thanks [user] and [user]!"


def test_handles_are_redacted():
    assert redact_pii("Add @CryptoMentor_Official on Telegram") == "Add [user] on Telegram"


def test_handle_rule_leaves_times_alone():
    assert redact_pii("We met @5pm") == "We met @5pm"


@pytest.mark.parametrize(
    "wallet",
    [
        "1BoatSLRHtKNngkdXEeobR76b53LETtpyT",  # bitcoin legacy
        "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy",  # bitcoin p2sh
        "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",  # bitcoin bech32
        "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",  # ethereum
        "TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7",  # tron
    ],
)
def test_wallet_addresses_are_redacted(wallet):
    assert redact_pii(f"Send USDT to {wallet} now") == "Send USDT to [wallet] now"


@pytest.mark.parametrize("card", ["4111 1111 1111 1111", "4111-1111-1111-1111", "3782 822463 10005"])
def test_card_numbers_are_redacted(card):
    assert redact_pii(f"I gave them card {card} by mistake") == "I gave them card [number] by mistake"


def test_ssn_is_redacted():
    assert redact_pii("They asked for my SSN 123-45-6789 to verify") == "They asked for my SSN [number] to verify"


def test_long_account_numbers_are_redacted():
    assert redact_pii("Wire to account 004512345678 at Chase") == "Wire to account [number] at Chase"


def test_cfpb_amounts_keep_their_value():
    assert redact_pii("I sent {$1200.00} and then {$25,000.00}") == "I sent $1200.00 and then $25,000.00"


def test_cfpb_masks_become_placeholders():
    text = "On XX/XX/XXXX, XXXX XXXX from XXXX called. In XX/XX/2023 they took card XXXX1234."
    assert redact_pii(text) == (
        "On [date], [redacted] from [redacted] called. In [date] 2023 they took card [number]."
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Hi, my name is Sarah Connor and I got scammed", "Hi, my name is [name] and I got scammed"),
        ("a guy named Mike reached out", "a guy named [name] reached out"),
        ("her name was Jessica on the app", "her name was [name] on the app"),
        ("an agent called Mr. Thompson phoned", "an agent called Mr. [name] phoned"),
        ("I spoke with Dr. Alan Grant about it", "I spoke with Dr. [name] about it"),
    ],
)
def test_names_in_clear_patterns_are_redacted(raw, expected):
    assert redact_pii(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Please help.\n\nThanks,\nJohn", "Please help.\n\nThanks,\n[name]"),
        ("Please help. Cheers, Emily R.", "Please help. Cheers, [name]"),
        ("Thanks for reading,\nMike", "Thanks for reading,\n[name]"),
        ("That is all.\n-- Dave", "That is all.\n-- [name]"),
        ("Stay safe.\n\nDaniel", "Stay safe.\n\n[name]"),
        ("Stay safe.\n\nEdit", "Stay safe.\n\nEdit"),
        ("Stay safe out there.\n\nThanks Everyone", "Stay safe out there.\n\nThanks Everyone"),
    ],
)
def test_sign_off_names_are_redacted(raw, expected):
    assert redact_pii(raw) == expected


def test_dash_sign_off_survives_markdown_stripping():
    assert clean_text("Stay safe out there.\n\n- Daniel") == "Stay safe out there.\n\n[name]"


def test_name_rules_skip_platforms_and_brands():
    text = "a group named Telegram Elite, an app named CryptoMax, a lady named Jessica WhatsApp"
    assert redact_pii(text) == (
        "a group named Telegram Elite, an app named CryptoMax, a lady named [name] WhatsApp"
    )


def test_dollar_amounts_survive():
    text = "Lost $1,200.00, then $50k, then $12000000, then 3000 USDT and 1.5 BTC"
    assert redact_pii(text) == text


def test_dates_survive():
    text = "It started on 10/12/2023, then 2023-11-01, then March 3, 2024 at 10:30"
    assert redact_pii(text) == text


def test_platform_names_survive():
    text = (
        "They moved me from Tinder to WhatsApp, then Telegram and Signal. I paid via Zelle, "
        "Venmo, PayPal, Cash App and Western Union, and bought crypto on Coinbase and Binance."
    )
    assert clean_text(text) == text


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


@pytest.mark.parametrize("body", ["[removed]", "[deleted]", "  ", "", "[removed by reddit]"])
def test_removed_deleted_and_empty_bodies(body):
    assert is_removed_or_empty(body)


def test_real_body_is_not_removed():
    assert not is_removed_or_empty(STORY)


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("[META] New rules for posting", STORY),
        ("Mod post: please read before posting", STORY),
        ("Weekly thread: is this a scam?", STORY),
        ("Quick question", "Your post was removed. I am a bot, and this action was performed automatically."),
    ],
)
def test_meta_posts_are_detected(title, body):
    assert is_meta_post(title, body)


def test_story_mentioning_a_moderator_is_not_meta():
    assert not is_meta_post("Fake Discord moderator stole my NFT", STORY)


def test_link_only_post_is_detected():
    raw = "https://suspicious-crypto-exchange.example.com/invite?code=abc123xyz\n\nanyone know this?"
    assert is_link_or_screenshot_only("Is this legit?", raw, clean_text(raw))


def test_screenshot_only_post_is_detected():
    raw = "Got this text today, screenshot below. What do you think?\n\n![img](abc123)"
    assert is_link_or_screenshot_only("Weird text", raw, clean_text(raw))


def test_scam_question_with_link_and_short_context_is_detected():
    raw = (
        "Got this from a recruiter on LinkedIn offering a remote data entry job paying $45 an hour. "
        "They want me to install an app and buy equipment with a check they send. Link: "
        "https://jobs-example-recruit.com/apply"
    )
    assert is_link_or_screenshot_only("Is this a scam?", raw, clean_text(raw))


def test_long_story_with_a_link_is_kept():
    raw = STORY + " Here is the site they used: https://fake-exchange.example.com"
    assert not is_link_or_screenshot_only("Is this a scam?", raw, clean_text(raw))


def test_story_without_links_is_never_link_only():
    assert not is_link_or_screenshot_only("Is this a scam?", "Short text", "Short text")


def test_too_short_posts_are_dropped():
    kept, stats = clean_posts([make_post("reddit-a", body="Got scammed on Zelle for $40. Lesson learned.")])
    assert kept == []
    assert stats["too_short"] == 1


def test_dedupe_by_id():
    kept, stats = clean_posts([make_post("reddit-a"), make_post("reddit-a")])
    assert [p["id"] for p in kept] == ["reddit-a"]
    assert stats["duplicate_id"] == 1


def test_dedupe_by_normalised_body():
    repost = STORY.upper().replace(" ", "   \n ")
    kept, stats = clean_posts([make_post("reddit-a"), make_post("reddit-b", body=repost, title="Repost")])
    assert [p["id"] for p in kept] == ["reddit-a"]
    assert stats["duplicate_text"] == 1


def test_body_fingerprint_ignores_case_and_whitespace():
    assert body_fingerprint("Hello  World\n") == body_fingerprint("hello world")
    assert body_fingerprint("hello world") != body_fingerprint("hello there")


def test_build_text_with_and_without_title():
    assert build_text("Title", "Body") == "Title\n\nBody"
    assert build_text("", "Body") == "Body"


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------


def test_end_to_end(tmp_path, capsys):
    story_with_contacts = STORY + "\n\nTheir number was +1 (555) 123-4567, email support@fake-ex.com."
    raw_posts = [
        make_post("reddit-1", body=story_with_contacts),
        make_post("reddit-1"),
        make_post("reddit-2", body="[removed]"),
        make_post("reddit-3", title="[Meta] Subreddit rules update"),
        make_post("reddit-4", title="Is this a scam?", body="https://i.redd.it/abc123.jpg"),
        make_post("reddit-5", body="Too short to be a story."),
        make_post("reddit-6", title="Crosspost", body=story_with_contacts.lower()),
        {
            **make_post("cfpb-7", title=""),
            "source": "cfpb",
            "score": None,
            "body": (
                "On XX/XX/XXXX I was contacted by XXXX on WhatsApp claiming to be an investment advisor. "
                "Over the next month I transferred {$4500.00} from my XXXX account to a crypto platform "
                "they recommended. When I asked to withdraw, they demanded a fee of {$900.00}. My bank "
                "refused to reverse the transfers even though I reported it as fraud within a week."
            ),
        },
    ]
    raw_path = tmp_path / "raw" / "posts.jsonl"
    out_path = tmp_path / "clean" / "posts.jsonl"
    raw_path.parent.mkdir()
    raw_path.write_text("".join(json.dumps(p) + "\n" for p in raw_posts))

    clean.main(["--input", str(raw_path), "--output", str(out_path)])

    rows = [json.loads(line) for line in out_path.read_text().splitlines()]
    assert [r["id"] for r in rows] == ["reddit-1", "cfpb-7"]
    assert all(set(r) == {"id", "source", "text", "n_chars"} for r in rows)
    assert all(r["n_chars"] == len(r["text"]) for r in rows)

    reddit, cfpb = rows
    assert reddit["text"].startswith("Lost $1,700 to a Hinge match\n\nI met someone on Hinge")
    assert "[phone]" in reddit["text"] and "[email]" in reddit["text"]
    assert "$1,200" in reddit["text"] and "Zelle" in reddit["text"]

    assert cfpb["source"] == "cfpb"
    assert cfpb["text"].startswith("On [date] I was contacted by [redacted] on WhatsApp")
    assert "$4500.00" in cfpb["text"] and "$900.00" in cfpb["text"]

    summary = capsys.readouterr().out
    assert "input posts:   8" in summary
    for reason in ("duplicate_id", "removed_or_empty", "meta_post", "link_or_screenshot_only",
                   "too_short", "duplicate_text"):
        assert f"dropped {reason}: 1" in summary
    assert "output posts:  2" in summary
