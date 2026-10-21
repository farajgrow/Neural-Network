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

## Neural network (v1)

A small GRU (`analysis/model/`, about 5,900 parameters) reads the steps seen so far plus the scam type. It has two outputs: the next step, and the probability that the story ends with the victim losing money. It was compared with simple baselines using 5-fold cross-validation **split by story**, so no story appears in both training and testing. Folds are stratified by scam type and the seed is fixed. Each test story contributes all of its prefixes. Rerun with `python -m analysis.model.train` (about 5 s on a laptop CPU).

**Result in one line: the GRU matches the baselines. It does not beat them.** On 487 stories, a first-order Markov table and a logistic regression already capture what the GRU learns.

### Setup

- **Inputs:** the collapsed script steps with `money_lost` and `victim_exits` removed, so the model never sees the ending. **Next-step targets:** the 13 script steps plus `END` ("the story stops here"). 487 stories give 1,440 prefixes, and `END` is the most common target (34%).
- **Loss label:** taken from the story and applied to every prefix of it. Only known outcomes count (277 stories: 101 lost, 176 escaped). Unknown-outcome stories still train the next-step head, and the loss head is masked for them.
- **Equal tuning budget:** the GRU uses 15% of the training stories for early stopping. Each baseline gets that same split to choose its one setting (Markov backoff strength from {1, 3, 10, 30}, logistic L2 from {0.001, 0.01, 0.1}). The GRU hyperparameters were set once and were not tuned against test folds. A Markov baseline with weak smoothing, as in an earlier draft, loses on log loss for reasons unrelated to the network, so that comparison would have been unfair.

### Next step (mean ± std over 5 folds, 14 classes)

| Model | Top-1 | Top-3 | Log loss | Top-1, 1 step seen | Top-1, 4+ steps seen |
|---|---|---|---|---|---|
| Most frequent next step | 0.338 ± 0.010 | 0.626 ± 0.020 | 2.069 ± 0.057 | 0.086 ± 0.017 | 0.657 ± 0.081 |
| Markov, last step | 0.498 ± 0.033 | 0.727 ± 0.026 | 1.534 ± 0.099 | 0.275 ± 0.038 | 0.676 ± 0.071 |
| Markov, last step + scam type | **0.545 ± 0.031** | 0.753 ± 0.026 | 1.467 ± 0.100 | **0.388 ± 0.030** | 0.668 ± 0.074 |
| Markov, last 2 steps + type | **0.545 ± 0.034** | **0.758 ± 0.033** | **1.466 ± 0.108** | 0.378 ± 0.040 | 0.666 ± 0.079 |
| **GRU** | 0.536 ± 0.036 | 0.754 ± 0.033 | 1.470 ± 0.092 | 0.361 ± 0.040 | 0.666 ± 0.097 |

Paired per fold on the same test stories, GRU minus Markov (last step + type): top-1 −0.009 ± 0.011, log loss +0.003 ± 0.019. That is a tie, with the GRU slightly behind on top-1.

### Loss risk (all prefixes of known-outcome test stories)

| Model | ROC AUC | Brier | Log loss |
|---|---|---|---|
| Training base rate | 0.500 ± 0.000 | 0.242 ± 0.012 | 0.677 ± 0.024 |
| Logistic regression (steps seen + type) | 0.848 ± 0.044 | 0.156 ± 0.021 | 0.484 ± 0.058 |
| **GRU** | 0.845 ± 0.057 | 0.153 ± 0.031 | 0.475 ± 0.100 |

Paired per fold, GRU minus logistic: AUC −0.003 ± 0.029, Brier −0.003 ± 0.016. Another tie. For reference, the rule "has the victim paid yet?" alone gets AUC 0.75.

Calibration (out-of-fold predictions pooled, 917 prefixes; predicted → observed loss rate):

| Bin | Logistic n | Logistic pred → obs | GRU n | GRU pred → obs |
|---|---|---|---|---|
| 0.0–0.2 | 249 | 0.13 → 0.08 | 284 | 0.08 → 0.12 |
| 0.2–0.4 | 314 | 0.30 → 0.31 | 317 | 0.29 → 0.31 |
| 0.4–0.6 | 141 | 0.46 → 0.46 | 73 | 0.48 → 0.42 |
| 0.6–0.8 | 100 | 0.73 → 0.92 | 44 | 0.71 → 0.61 |
| 0.8–1.0 | 113 | 0.91 → 0.85 | 199 | 0.93 → 0.90 |

Both models are roughly calibrated. The GRU pushes more prefixes into the extreme bins.

### Early warning: loss risk from only the first 1, 2 or 3 steps

This is the product question. One prediction is made per known-outcome test story from its first k steps. Stories shorter than k use all their steps. ROC AUC, with Brier in brackets:

| Model | First 1 step | First 2 steps | First 3 steps |
|---|---|---|---|
| Base rate | 0.500 (0.234) | 0.500 (0.234) | 0.500 (0.234) |
| Logistic regression | **0.736 ± 0.056** (0.189) | **0.832 ± 0.086** (0.153) | **0.911 ± 0.028** (0.121) |
| GRU | 0.679 ± 0.083 (0.202) | 0.821 ± 0.108 (0.155) | 0.908 ± 0.036 (0.113) |

Read these numbers with care. 395 of 487 stories start with `initial_contact`, so the first-step number mostly reflects the scam type and the few stories that open with a payment or with suspicion. Most stories are short (median 3 script steps), so "first 3 steps" is often the whole story. That includes `victim_suspicious` and payment steps, which sit right next to the outcome. Within the first 3 steps, 27% of known-outcome stories already contain a payment. Early warning after 1 step is weak for both models, and the logistic regression is at least as good as the GRU there.

### What the GRU adds, and what it doesn't

- **It doesn't add accuracy.** With 13 steps, median story length 3, and about 390 training stories per fold, a step plus the scam type already explains nearly everything predictable. A second-order Markov model does no better than a first-order one, which suggests there is little long-range order for a recurrent network to find. On the loss side, the bag-of-steps logistic regression is as good as the GRU, so the order of steps adds little beyond which steps happened.
- **What it does add:** one model for both questions, smooth probabilities with no hand-tuned backoff, and a design that would scale to longer histories and richer inputs (for example message text embeddings) if the data supported them. Because the GRU is causal, one pass over a story trains on every prefix at once.
- **For the app today,** the Markov table is just as accurate and easier to explain. The GRU is a credible v1 to grow into, not an upgrade yet.

### Caveats

- **Small data:** 487 stories, only 277 with known outcomes (101 losses). Fold-to-fold std is 0.03–0.10, larger than any gap between the GRU and the baselines.
- **Labels are LLM-extracted** from **self-reported** Reddit posts that lean toward dramatic cases. The models learn the extractor's habits as well as the scams.
- **The loss head is partly circular.** Payment steps are inputs, and you can't lose money without paying (§1: 92% of stories with a payment lost money). Much of the AUC comes from "has the victim paid yet?". `victim_suspicious` is the same kind of near-outcome signal. A high AUC here does not mean the model foresees losses before the victim commits.
- **`END` is partly an artefact of storytelling.** It means the post stopped there, which is not the same as the scam stopping.

### What more data would change

- With thousands of stories, especially in the small types (pig_butchering 17, romance 26), the per-type and second-order contexts would stop being sparse. That is where a GRU could start to pull ahead of count tables, since it shares strength across similar contexts.
- More known outcomes would allow an honest **pre-payment** evaluation: train and test loss risk only on prefixes before any payment. That is the warning that matters, and it is too thin to measure with 277 outcomes.
- Richer inputs per step (the message text, amounts, platform) would give the sequence model something the Markov table can't use. Without them it has no edge to exploit.
