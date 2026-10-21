# Scam Script Analysis: 2-Hour Build Plan

Shared plan for both of us and our agents. It lives in the repo root on `main`. Implementation choices are up to each agent. This file covers what gets built, who owns it, and when things need to land so the other person isn't blocked.

## Goal

Turn real scam victim stories into structured event sequences. Cluster them into scam scripts, build a graph per script, and find the pinch points (where victims commit and losses jump) and escape points (where victims get out). Ship a polished demo with a live matcher: paste a suspicious message, see which scam it is, where you are in the script, and what usually happens next.

Optimise for a working demo at 2:00 and a codebase that looks deliberate.

## Who owns what

| | Faraj | Partner |
|---|---|---|
| Area | Frontend, demo polish, API, sequence clustering, graph construction | Scraping, cleaning, LLM extraction, live matcher logic |
| Branch | `app` | `data` |
| Folders | `app/` (frontend + API), `analysis/` | `pipeline/`, `data/` |
| Produces | Graphs file, API, UI | Clean stories file, matcher function |
| Consumes | Stories file, matcher function | Nothing (fully independent after T0) |

Partner also owns the matcher logic because it is the same problem as extraction (text in, steps out using the same vocabulary). Faraj wires it into the API and UI.

## The contract

Agreed at T0 and written into the repo as a shared schema, in whatever form the agents choose. Only change it with both people agreeing, because both sides build against it.

**Step vocabulary (fixed list, every event must use one):** initial_contact, platform_switch, trust_building, authority_claim, urgency_pressure, fake_proof_shown, small_payment, large_payment, credentials_shared, remote_access_granted, withdrawal_blocked, victim_suspicious, third_party_intervened, victim_exits, money_lost

**Scam types:** pig_butchering, fake_job, impersonation (bank, government, tech support), marketplace, romance, other

**Story** (one per extracted post):
- id, source (reddit or cfpb), scam_type
- events: chronological list, each with step, actor (scammer, victim, third_party), and a short detail
- outcome: lost_money, escaped, or unknown
- amount_lost_usd (optional)
- snippet: 1 to 2 sentence anonymised summary for the UI

**Stories file:** all clean stories in one file at a fixed path in `data/`. This is the main handoff from Partner to Faraj.

**Matcher interface:** takes pasted text. Returns scam_type, confidence, steps_so_far, likely_next_step, and a one or two sentence piece of advice. Faraj's API adds loss_rate_if_continues from the real graph.

**Graphs file** (Faraj produces, only Faraj consumes): per scam type, the nodes (step, count, loss rate), edges (from, to, count), top 3 pinch points, top 3 escape points, cluster assignments, and a lookup from step to story ids for the UI.

## The one rule

Don't destroy the other person's work:
- Only edit files in your own folders. If something in the other person's area needs changing, tell them.
- Never force push, rebase, or rewrite history on `main` or the other person's branch.
- Never delete, overwrite, or regenerate files in the other person's folders, including data files.
- Don't change the shared contract without both people agreeing.

## Git setup

Branch names in this repo: `Faraj-branch` is `app` and `Arya-branch` is `data`. Arya is Partner. The contract is in `contract/`, and the stories file is `data/stories.json`.

- `main` holds the contract, this plan, and the mock stories file. Merge into it only at the merge points below.
- `app` (Faraj) and `data` (Partner) branch from `main` after T0.
- Each agent task runs in its own worktree off your branch. Merge worktrees back into your branch when the task is done, then into `main` at merge points.
- Commit the stories file to git (it's small) so the handoff is just a merge.

## Merge points

These are what matter most for working together. Hit these times even if the work feels unfinished.

| Time | Merge | Why it matters |
|---|---|---|
| 0:15 | T0 to `main` | Contract and mock stories exist, so both people can start |
| 0:50 | M1: `data` to `main`, about 50 real stories | Faraj tests clustering and graphs on real data instead of mock |
| 1:15 | M2: `data` to `main`, full dataset (about 500) | Real graphs and findings for the demo |
| 1:25 | M3: `data` to `main`, matcher function | Live matcher goes in the UI |
| 1:35 | M4: `app` to `main` | Everything together, integration fixes on `main` |

## Tasks

### T0. Together, 0:00 to 0:15

- [ ] T0.1 Create repo, folders, and branches
- [ ] T0.2 Write the contract into the repo as a shared schema
- [ ] T0.3 Partner hand-writes about 10 mock stories in the contract format, spread across 2 scam types with a mix of outcomes, saved as the stories file
- [ ] T0.4 Add this plan to the repo root
- [ ] T0.5 Merge to `main`, create `app` and `data` branches

### Partner tasks

| ID | Time | Task | Done when | Blocks |
|---|---|---|---|---|
| P1 | 0:15 to 0:35 | Scrape r/Scams (top and new) via the public JSON endpoint, about 500 posts with substantial body text. Fallback if blocked: CFPB complaint API, narratives mentioning scams or fraud. Save raw posts. | Raw posts saved locally | P2 |
| P2 | 0:20 to 0:40 | Cleaning: dedupe, strip markdown and links, filter out short or non-story posts, remove PII (usernames, emails, phone numbers, real names). Can start on a partial P1 sample in a separate worktree. | Clean text ready for extraction | P3 |
| P3 | 0:35 to 0:50 | Extraction: Claude Haiku turns each post into a Story using the fixed vocabulary. Run concurrently, cache every result so reruns are free, validate against the contract, drop and count failures. Run on the first 50 posts first. | 50 valid stories in the stories file | **M1**, Faraj F3 |
| P4 | 0:50 to 1:15 | Run extraction on the full set. Hand-check 20 stories against the source posts and note accuracy (this becomes a README stat). | Full stories file, accuracy number | **M2** |
| P5 | 1:00 to 1:25 | Matcher logic: reuse the extraction prompt so that given a pasted message or conversation it returns the matcher interface. Use Sonnet for quality. Test it on the 2 demo messages from F8. | Callable matcher function that works on the demo messages | **M3**, Faraj F7 |
| P6 | 1:25 to 1:40 | Tests for cleaning and extraction validation. One command that runs scrape, clean, extract end to end. Write the data section of the README (sources, counts, cleaning steps, accuracy). | Tests pass, pipeline runs from one command | README |

### Faraj tasks

| ID | Time | Task | Done when | Needs |
|---|---|---|---|---|
| F1 | 0:15 to 0:35 | API and frontend scaffold. API serves scam types, graph per type, and stories filtered by type and step. Frontend: React + Vite + React Flow, layout with graph area, side panel, matcher panel. | API serving mock-derived data, UI shell running | T0 |
| F2 | 0:15 to 0:45 | Graph construction (separate worktree from F1). Per scam type: transition graph, node counts and loss rates, top 3 pinch points (biggest loss rate jump versus preceding step), top 3 escape points (steps most often followed by victim_exits). Write the graphs file. Build and test on mock stories. | Graphs file generated from mock stories | T0 |
| F3 | 0:50 to 1:10 | Sequence clustering: cluster event sequences within and across scam types to find script variants (for example, pig butchering via dating app vs via wrong-number text). Compare clusters with the LLM's scam_type labels. Add cluster ids to the graphs file. Run on the M1 sample. | Clusters in graphs file, one interesting finding | **M1** |
| F4 | 0:35 to 1:00 | Graph view: node size by count, colour by loss rate (green to red), edge width by frequency, scam type switcher, readable left-to-right layout ordered by typical step position. | Graph looks good on mock data | F1, F2 |
| F5 | 1:00 to 1:20 | Interaction: click a node to see real story snippets in the side panel. Red badges on pinch points, green on escape points. Toggle or colouring for script variant clusters from F3. | Clicking around feels good | F4 |
| F6 | 1:15 to 1:25 | Rerun graphs and clustering on the full dataset. Pull 2 or 3 headline findings with real numbers. | Real graphs, findings written down | **M2** |
| F7 | 1:20 to 1:35 | Matcher UI: paste box, result card (scam type, current step, next step, loss rate if they continue, advice), and highlighted matched steps on the graph. Build against a hardcoded response first, then swap in P5. | Live matcher works in the UI | **M3** |
| F8 | 0:45 (5 min) | Write the 2 demo messages (a fake crypto mentor, a fake job offer) and send them to Partner for P5 testing. | Partner has them | Nothing |
| F9 | 1:35 to 1:50 | Integration on `main` and demo polish: loading states, empty states, spacing, a clean header with project name and one-line description. README with pitch, architecture diagram, how to run, findings, screenshots. | Repo looks finished | M4 |

### Both, 1:50 to 2:00

- [ ] Fresh clone, run setup from the README, confirm it works
- [ ] Rehearse the demo twice with both demo messages

## Demo script (2 minutes)

1. "Scams follow scripts. We turned [N] real victim stories into event sequences and mapped them."
2. Open the pig butchering graph and point at the top pinch point: "Once victims make a small payment, loss rate goes from X% to Y%."
3. Show a script variant from clustering: "There are actually two versions of this scam, and they hook people at different points."
4. Point at escape points: "The people who got out mostly did it here."
5. Paste a demo message into the matcher. It names the scam, the current step, what comes next, and the loss rate if they keep going.

## Cut lines

- Partner behind at 0:50: merge whatever stories exist, even 20. Faraj keeps building.
- Full dataset not ready at 1:15: demo on the partial set and say so.
- Clustering not giving clean results by 1:10: drop it from the demo, keep it in the code and README as exploratory.
- Matcher not working by 1:30: serve cached responses for the 2 demo messages.
- Never cut: the contract, caching, README, or the merge points.

## Honest framing

Say "pinch points associated with loss" and "escape points", not "causal". The data is self-reported and skews toward people who lost money and posted about it. Saying that in the README makes the project more credible.
