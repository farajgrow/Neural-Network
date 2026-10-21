# Jev matcher: handoff for Faraj's Claude Code

From Arya (pipeline/ and data/). Read this before touching the matcher in the API or UI. It is written for Claude Code: paste section 6 into parallel sessions.

## 1. Where things are (2026-10-21)

- **main** (`d2dcb5a`) has M1 to M5: 487 stories, the Jev matcher (M3), data docs and scraper retries (M4), and the hand spot check (M5, 96.2% field accuracy).
- **Faraj-branch** has the API, the UI, clustering and findings. It last merged main at M2, so it **does not have Jev yet**.
- **Trial merge** of Faraj-branch into main (done by Arya, nothing committed): no conflicts, 281 tests pass (pipeline, analysis, API), and `POST /api/match` returns a valid result.
- **The next merge into main is "M6: app to main"** (PLAN.md calls it M4, but M4 and M5 are already used for data merges).

## 2. Ownership rules for every agent

- Faraj owns `app/`, `analysis/` and the root `README.md`. Arya owns `pipeline/` and `data/`. **Agents must not edit `pipeline/` or `data/`.** Changes to `contract/` need both of you to agree.
- One git worktree per agent, cut from **Faraj-branch** (pass the source branch explicitly; the default may be main). Before starting, run `git branch -a` and `git worktree list` to check that nobody already has a branch for the same task. Twice on Arya's side, an agent rebuilt work that already existed.
- Merge finished work into Faraj-branch with `git merge --no-ff -m "Merge F10: ..."`. No rebase, no force push, and ask before pushing or merging into main.
- Never print or commit `TYPESAFE_API_KEY` or `ANTHROPIC_API_KEY`. `.env` and `pipeline/.cache/` are gitignored.

## 3. Setup on Faraj's machine

```bash
git checkout Faraj-branch && git merge --no-ff -m "Merge main: M3-M5 Jev matcher, data docs, hand spot check" origin/main
.venv/bin/pip install -r app/requirements.txt -r pipeline/requirements.txt   # pipeline/requirements.txt adds typesafe-sdk (0.7.5 works)
echo 'TYPESAFE_API_KEY=...' >> .env    # key from Arya, sent privately
.venv/bin/python -m pytest -q          # expect 281 passed, 4 skipped
.venv/bin/python -m pipeline.matcher "$(cat app/demo/fake_job.txt)"   # stderr should say [matcher source: jev]
```

`app/requirements.txt` doesn't list `typesafe-sdk`. Without it (or without the key) the matcher quietly uses Sonnet or the rules. Nothing errors, so check the `source` value.

## 4. How the Jev matcher works (`pipeline/matcher.py`)

The API calls `pipeline.matcher.match_with_source(text)`, which returns `(result, source)`. `result` always validates against `contract/matcher.schema.json`: `scam_type`, `confidence`, `steps_so_far`, `likely_next_step`, `advice`.

**Fallback order:** each step falls through to the next on any error or invalid answer.

| # | Path | `source` returned | When |
|---|---|---|---|
| 1 | Disk cache `pipeline/.cache/matcher/<sha256>.json` | `cache` | The same text got a valid model answer before |
| 2 | **Jev** (TypeSafe System One, model `jev-latest`) | `jev` | `TYPESAFE_API_KEY` is in the environment or `.env` |
| 3 | Sonnet (`claude-sonnet-5-5`, forced tool use) | `api` | `ANTHROPIC_API_KEY` is set |
| 4 | Stored answers in `pipeline/demo_responses.json` | `demo` | The text is one of *pipeline's* two demo messages |
| 5 | Offline regex rules | `rules` | Always works |

**What Jev is asked.** `jev_questions()` builds one System One request with state `{context, pasted_text}`:
- `scam_type`: a Choice over the 6 scam types, using the guides in `pipeline/prompts.py`.
- `is_scam`: a Noul, "does this show signs of a scam attempt".
- `step:<step>`: one Noul per vocabulary step (15 of them). They are worded differently by who acts:
  - **Scammer moves** (platform switch, urgency and so on) count if the scammer does them or asks for them.
  - **Victim actions** (`small_payment`, `large_payment`, `credentials_shared`, `remote_access_granted`, `money_lost`) count only if the person has already done them. Being asked doesn't count.
  - **Reactions** (`victim_suspicious`, `third_party_intervened`, `victim_exits`) count only if they really happen, not when the scammer pretends to be a bank or police.
- `next_step`: a Choice over script steps only (no reactions). It asks what the scammer will get the person to do next.

**How code builds the result** (`jev_result`):
- A step counts as seen when its Noul is above 0.5.
- **`steps_so_far` is sorted by where each step usually falls in the 487 stories, not by the order it appears in the text.** The API's `current_step` is the last item of that list.
- `confidence` = P(chosen scam type) × P(is a scam).
- If P(is a scam) is 0.5 or below, the result is `other`, with confidence equal to P(is a scam) and a generic low-risk advice line.
- `likely_next_step` is Jev's most likely next step among the steps not seen yet. If Jev gives none of them at least 0.05, it falls back to the most common transition in `data/stories.json`. Victim reactions are never predicted.
- `advice` comes from fixed templates per scam type (`ADVICE`), not from the model.

**Speed:** a live Jev call on the demo messages took 0.3 to 0.9 s. The client timeout is 15 s with 1 retry, so a hung Jev call can take about 30 s before Sonnet is tried.

**Live results on Faraj's demo files** (no cache):
- `app/demo/crypto_mentor.txt`: `pig_butchering`, 0.99, steps initial_contact → trust_building → platform_switch → fake_proof_shown → urgency_pressure, next `small_payment`.
- `app/demo/fake_job.txt`: `fake_job`, 0.99, steps initial_contact → trust_building → authority_claim → platform_switch → fake_proof_shown → urgency_pressure, next `small_payment`.

## 5. Gaps between the API/UI and Jev (the actual work)

1. **The API doesn't know the `jev` source.** In `app/api/main.py`, `_MATCHER_SOURCES = {"api": "live", "cache": "cache", "demo": "cache", "rules": "fallback"}` is missing `"jev"`. Jev results get `source: "live"` and `source_detail: "matcher_jev"`, and the comment block says "live = Sonnet answered". Fix: add a `jev` source (or keep `live` and label it), update the `MatchResult.source` type in `app/web/src/api.ts`, the comment, and `test_match_source_mapping`.
2. **The timeout budget.** `SCAM_MATCH_TIMEOUT` defaults to 20 s, which is shorter than the worst case of Jev plus retry plus Sonnet. That's fine (the API falls back to the demo cache, then the rules), but the docs should say so. Jev normally answers in under 1 s.
3. **Cached answers show as `cache`.** After the first live call, the same text is served from `pipeline/.cache/matcher/`, so during the demo the badge says `cache`, not `jev`. Either show `source_detail` or accept it. To force a fresh call, delete that folder.
4. **Two different sets of demo messages.** `pipeline/demo_messages.json` and `app/demo/*.txt` are different texts. Pipeline's stored demo answers (path 4) never match Faraj's demo files. Faraj's own `app/api/demo_cache.json` covers them (it was hand-written with no key). With Jev live, both demo files get real answers anyway.
5. **Step order in the UI.** The result card and the graph highlight treat `steps_so_far` as text order. With Jev it is typical story order (see section 4). That suits the graph path, but any "← now" wording should be read as "furthest step reached".
6. **The docs still say Sonnet.** The root `README.md` architecture diagram says `pipeline.matcher<br/>Claude Sonnet + offline fallback`, and the run section says to put `ANTHROPIC_API_KEY` in `.env`. Both should say Jev first (`TYPESAFE_API_KEY`), then Sonnet, then rules.
7. **Optional, needs Arya:** Jev already returns per-step probabilities and scam-type probabilities. The contract (`additionalProperties: false`) doesn't carry them. If you want confidence bars per step in the UI, agree on an optional `details` field. Arya would expose it from `pipeline/matcher.py`; agents must not edit `pipeline/` themselves.

## 6. Parallel agent prompts

Run A, B and C in parallel (each in its own worktree from Faraj-branch). Run D after they are merged.

**Agent A: API source mapping and tests** (only `app/api/`)
```
Read FARAJ_JEV_HANDOFF.md, contract/README.md and app/api/main.py. Only edit app/api/. Never edit pipeline/, data/ or contract/. No push, no rebase.
Check `git branch -a` and `git worktree list` first, then create a worktree from Faraj-branch: git worktree add -b faraj/f10-api-jev ../scam-wt-f10-api Faraj-branch
1. Add the "jev" matcher source: _MATCHER_SOURCES maps "jev" -> "jev". Update the comment block that lists the sources (jev = Jev answered just now, live = Sonnet).
2. Extend test_match_source_mapping with ("jev", "jev"). Add a test that a mocked match_with_source returning source "jev" gives source "jev" and source_detail "matcher_jev".
3. Note in the docstring that Jev normally answers in under 1 s and that SCAM_MATCH_TIMEOUT (20 s) can cut off Jev plus retry plus Sonnet, after which the demo cache and then the rules answer.
4. Run .venv/bin/python -m pytest -q and commit on faraj/f10-api-jev. Don't merge; report back.
```

**Agent B: UI for the Jev source** (only `app/web/`)
```
Read FARAJ_JEV_HANDOFF.md, app/web/src/api.ts and app/web/src/components/MatcherPanel.tsx. Only edit app/web/. No push, no rebase.
Check `git branch -a` and `git worktree list` first, then: git worktree add -b faraj/f11-ui-jev ../scam-wt-f11-ui Faraj-branch
1. Add 'jev' to MatchResult.source in api.ts.
2. In ResultCard, show a clear source tag: "jev" (live Jev), "sonnet" for live, "cached", "offline rules" for fallback, "mock". Keep source_detail as the tooltip. Match the existing retro style in index.css.
3. steps_so_far from Jev is ordered by typical story position, not text order. Make sure the "← now" marker reads as the furthest step reached, and that the graph highlight still draws the path in that order.
4. Run npm run build (and lint if configured) in app/web, then commit. Don't merge; report back.
```

**Agent C: docs** (only the root `README.md` and `app/README.md`)
```
Read FARAJ_JEV_HANDOFF.md, README.md, app/README.md and the Matcher section of pipeline/README.md. Only edit README.md and app/README.md. No push, no rebase.
Check `git branch -a` and `git worktree list` first, then: git worktree add -b faraj/f12-docs-jev ../scam-wt-f12-docs Faraj-branch
1. Architecture diagram: the matcher node becomes "pipeline.matcher<br/>Jev, then Sonnet, then offline rules".
2. How to run: install both requirement files; TYPESAFE_API_KEY in .env for Jev (ANTHROPIC_API_KEY optional, for the Sonnet fallback); no key still works offline.
3. app/README.md: document the source values (jev, live, cache, fallback, mock) and that repeat texts come from the cache.
4. Commit. Don't merge; report back.
```

**Agent D: integration and M6** (after A, B and C are merged into Faraj-branch)
```
Read FARAJ_JEV_HANDOFF.md. On Faraj-branch: confirm main is merged in, run .venv/bin/python -m pytest -q (expect about 283 passed plus skips), then start the API and the UI and paste both app/demo/*.txt files into the matcher. Report scam type, steps, next step, loss rate and the source tag for each, with screenshots.
Then delete pipeline/.cache/matcher/ and check once more that the source says jev. Then stop and ask before merging Faraj-branch into main as "M6: app to main" and before any push.
```

## 7. Demo checklist

- Open on **fake job or impersonation**. Pig butchering has only 17 stories, so its graph is thin.
- Without a network, or if Jev fails, the demo messages still answer from `app/api/demo_cache.json`, and other text uses the offline rules.
- Rehearse from a fresh clone: clone, `pip install` both files, add `.env`, start uvicorn and `npm run dev`, then paste both demos.
