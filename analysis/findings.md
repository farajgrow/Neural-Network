# Findings

Generated from `analysis/graphs.json` on the real dataset: **487 r/Scams stories**, extracted into event sequences by an LLM (other 170, fake_job 105, impersonation 102, marketplace 67, romance 26, pig_butchering 17). Rerun with `python -m analysis.build`.

**Read these as associations in self-reported data, not causes.** The stories are what people chose to post on Reddit, and the posts lean toward dramatic cases. **210 of 487 stories (43%) have an unknown outcome**, and every loss rate below leaves them out, so the denominators ("known") are much smaller than the story counts. Steps and outcomes are LLM-extracted, not hand-labelled. Overall loss rate: 36% (101 lost / 277 known).

## 1. Paying is the point of no return

Across all types, the two biggest pinch points are both payments:

| Step | Loss rate at the step | Loss rate at the steps leading in | Known outcomes |
|---|---|---|---|
| small payment | **87%** (41 / 47) | 37% | 47 |
| large payment | **98%** (50 / 51) | 41% | 51 |

Stories with any payment step: 92% lost (82 / 89 known). Stories with no payment step: 10% lost (19 / 188 known). The same pattern shows up inside fake_job (small payment: 33% → 86%, 6 / 7 known) and marketplace (52% → 92%, 11 / 12 known).

Demo line: *"Once victims make even a small payment, the loss rate goes from 37% to 87%."*

Caveats: part of this is built in, since you can't lose money without paying something. What the data does show is that hardly anyone in these stories got out after paying. For pig_butchering alone the small-payment number is 3 / 3 known, so it is too small to quote. Use the all-types number and say so.

## 2. People escape by getting suspicious before they pay

`victim suspicious` is the top escape point in every scam type. In 71 of the 120 stories with an explicit exit, the exit comes straight after it. Timing is what matters:

| When the victim got suspicious | Stories | Loss rate |
|---|---|---|
| before any payment | 303 | **5%** (7 / 141 known) |
| only after paying | 46 | **89%** (34 / 38 known) |

Demo line: *"The people who got out mostly did it here: they got suspicious before sending money. Suspicion after paying almost never saved anyone."*

Caveats: `victim_suspicious` sits right next to the outcome, and the extractor may tag it more often in stories that ended well, so this is partly circular. The second escape point is `initial_contact` (12 exits out of 396 stories), which means people who stopped replying right away. Every other escape candidate has fewer than 10 exits.

## 3. Script variants: the same scam, two very different paths (small samples)

Clustering ignores steps that appear in more than 70% of stories (`initial_contact`, `victim_suspicious`), so the clusters split on the middle of the script.

**Across types the clusters do not follow the LLM's scam_type labels** (5 clusters, adjusted Rand index 0.06, about chance). The biggest cluster, *builds trust → fake proof → third party steps in* (190 stories), pulls from all six types and loses 63% (78 / 124 known). The *victim suspicious only* cluster (123 stories) loses 6% (2 / 36). The shape of the script says more about where a story ends than the name of the scam does.

Within types (from `clusters_by_type`):

| Type | Variant | Stories | Loss rate |
|---|---|---|---|
| fake_job | builds trust → fake proof | 49 | 34% (10 / 29) |
| fake_job | claims authority → urgency | 14 | 0% (0 / 7) |
| fake_job | first contact → victim suspicious only | 22 | 0% (0 / 5) |
| romance | first contact → builds trust → victim suspicious | 17 | 30% (3 / 10) |
| romance | builds trust → large payment → third party steps in | 9 | 100% (5 / 5) |
| pig_butchering | moves platform → victim suspicious | 7 | 0% (0 / 5) |
| pig_butchering | fake proof → large payment → withdrawal blocked | 5 | 100% (5 / 5) |

Demo line, framed honestly: *"There are two versions of this scam. In one the victim is moved to another app and gets suspicious there. In the other they see fake proof, pay big and then can't withdraw. In our data the first group never lost money and the second always did, though that's only a dozen stories."*

## Too small to claim

- Anything pig_butchering-specific: 17 stories, 13 known outcomes. The variant split above is 5 vs 7 stories.
- Romance variants: 26 stories, 15 known outcomes.
- Per-type pinch points with fewer than about 10 known outcomes at the step: pig_butchering (all three), romance large payment (6 known), fake_job small/large payment (7 and 9 known), marketplace large payment (6 known), `other` credentials shared (3 known).
- Cluster loss rates with `n_known` under about 10 (most within-type variants). The UI should show `n_known` next to any cluster loss rate.
- Escape points with 2 or 3 exits (everything except `victim suspicious` and, in the all-types graph, `initial_contact` and `urgency_pressure`).
