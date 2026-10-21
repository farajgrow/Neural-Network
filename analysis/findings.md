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

## Neural network (v2: next scam move)

A small GRU (`analysis/model/`, about 5,800 parameters) reads the steps seen so far plus the scam type. It has two outputs: **the scammer's next move**, and the probability that the story ends with the victim losing money. It is compared with simple baselines using 5-fold cross-validation **split by story**, so no story appears in both training and testing. Folds are stratified by scam type and the seed is fixed. Rerun with `python -m analysis.model.train` (about 7 s on a laptop CPU).

**Why v2:** v1 predicted "the very next step, or END". In these posts the most common next step by far is `victim_suspicious` (331 of 953 non-END transitions), because people post when they get suspicious, so v1 mostly predicted `victim_suspicious` or END, which says nothing about what the scammer will try next. v2 predicts the next scammer move instead. Next-step numbers for v1 and v2 are not comparable (different targets), so only v2 is reported here.

**Result in one line: the GRU ties the baselines. It does not beat them.** Predicting the scammer's next move is hard on this data: the best models get it right about 36% of the time (top-3: about 66%), and a Markov table with the scam type does as well as the GRU.

### Setup

- **Moves** are the vocabulary minus the victim reactions (`victim_suspicious`, `third_party_intervened`, `victim_exits`) and minus `money_lost`: 11 steps. This is the same rule as the app matcher's script steps minus `money_lost`.
- **Next-step target:** for each prefix, the next move that comes later in the story, **skipping reactions in between**. For `[initial_contact, victim_suspicious, small_payment]`, the prefix `[initial_contact]` has target `small_payment`. A prefix with no later move (for example one that ends in the victim getting suspicious and leaving) produces **no** next-step example. There is no `END` class. 487 stories give 1,440 prefixes, but only **554 next-step examples, from 315 stories**. Most common targets: `urgency_pressure` 95, `authority_claim` 88, `fake_proof_shown` 68, `trust_building` 62.
- **Inputs** keep every step except the outcomes (`money_lost`, `victim_exits`). **Reactions stay in the inputs on purpose:** `victim_suspicious` and `third_party_intervened` say a lot about what comes next and about loss risk, they are just never something to predict. The model never sees how a story ended.
- **Loss label:** unchanged from v1. It is taken from the story and applied to every prefix, **including prefixes with no later move**, so the loss head trains and is scored on exactly the same 917 known-outcome prefixes as v1 (277 stories: 101 lost, 176 escaped). The two heads therefore use different example sets; in the GRU each position is masked separately for each head.
- **Equal tuning budget:** the GRU uses 15% of the training stories for early stopping. Each baseline gets that same split to choose its one setting (Markov backoff strength from {1, 3, 10, 30}, logistic L2 from {0.001, 0.01, 0.1}). The GRU hyperparameters are the v1 ones, not tuned against test folds. Markov contexts are the last input step(s), exactly what the GRU sees, so a context can be a reaction.
- **Top-1 among unseen moves** removes moves already in the prefix before taking the top prediction, which is what the app shows. Only 4 of 554 targets repeat a move already seen, so it is close to plain top-1.

### Next scam move (mean ± std over 5 folds, 11 classes, 554 examples)

| Model | Top-1 | Top-1, unseen moves | Top-3 | Log loss | Top-1, 1 step seen | Top-1, 4+ steps seen |
|---|---|---|---|---|---|---|
| Most frequent move | 0.160 ± 0.036 | 0.172 ± 0.036 | 0.441 ± 0.025 | 2.216 ± 0.051 | 0.111 ± 0.072 | 0.235 ± 0.139 |
| Markov, last step | 0.283 ± 0.029 | 0.287 ± 0.033 | 0.619 ± 0.060 | 1.955 ± 0.095 | 0.248 ± 0.021 | 0.395 ± 0.197 |
| Markov, last step + scam type | 0.344 ± 0.029 | 0.350 ± 0.021 | **0.670 ± 0.056** | **1.770 ± 0.092** | **0.356 ± 0.051** | 0.357 ± 0.186 |
| Markov, last 2 steps + type | 0.358 ± 0.030 | **0.368 ± 0.023** | 0.658 ± 0.070 | 1.797 ± 0.113 | **0.356 ± 0.051** | 0.429 ± 0.330 |
| **GRU** | **0.359 ± 0.034** | 0.361 ± 0.024 | 0.664 ± 0.057 | 1.779 ± 0.079 | 0.334 ± 0.028 | **0.497 ± 0.126** |

Paired per fold on the same test stories, GRU minus Markov (last step + type): top-1 +0.015 ± 0.034, top-1 among unseen moves +0.011 ± 0.024, log loss +0.008 ± 0.055. That is a tie. The GRU's lead with 4+ steps seen rests on about 6 test examples per fold (31 in total), so it is noise, not a finding.

### Loss risk (all prefixes of known-outcome test stories, same 917 prefixes as v1)

| Model | ROC AUC | Brier | Log loss |
|---|---|---|---|
| Training base rate | 0.500 ± 0.000 | 0.242 ± 0.012 | 0.677 ± 0.024 |
| Logistic regression (steps seen + type) | 0.848 ± 0.044 | 0.156 ± 0.021 | 0.484 ± 0.058 |
| **GRU** | 0.839 ± 0.055 | 0.153 ± 0.031 | 0.481 ± 0.100 |

The baselines are identical to v1, which confirms the loss examples did not change. Paired per fold, GRU minus logistic: AUC −0.009 ± 0.021, Brier −0.003 ± 0.013. Another tie; the GRU's loss head moved slightly (v1: AUC 0.845) only because it now shares its network with a different next-step task. For reference, the rule "has the victim paid yet?" alone gets AUC 0.75.

Calibration (out-of-fold predictions pooled, 917 prefixes; predicted → observed loss rate):

| Bin | Logistic n | Logistic pred → obs | GRU n | GRU pred → obs |
|---|---|---|---|---|
| 0.0–0.2 | 249 | 0.13 → 0.08 | 337 | 0.08 → 0.12 |
| 0.2–0.4 | 314 | 0.30 → 0.31 | 285 | 0.30 → 0.34 |
| 0.4–0.6 | 141 | 0.46 → 0.46 | 57 | 0.48 → 0.46 |
| 0.6–0.8 | 100 | 0.73 → 0.92 | 42 | 0.68 → 0.64 |
| 0.8–1.0 | 113 | 0.91 → 0.85 | 196 | 0.94 → 0.91 |

Both are roughly calibrated. The GRU pushes more prefixes into the extreme bins.

### Early warning: loss risk from only the first 1, 2 or 3 steps

One prediction per known-outcome test story from its first k steps (all steps if shorter). ROC AUC, with Brier in brackets:

| Model | First 1 step | First 2 steps | First 3 steps |
|---|---|---|---|
| Base rate | 0.500 (0.234) | 0.500 (0.234) | 0.500 (0.234) |
| Logistic regression | **0.736 ± 0.056** (0.189) | **0.832 ± 0.086** (0.153) | **0.911 ± 0.028** (0.121) |
| GRU | 0.692 ± 0.097 (0.196) | 0.815 ± 0.103 (0.149) | 0.907 ± 0.025 (0.112) |

Read these with care. 395 of 487 stories start with `initial_contact`, so the first-step number mostly reflects the scam type. Most stories are short (median 3 steps), so "first 3 steps" is often the whole story, including `victim_suspicious` and payment steps that sit right next to the outcome.

### Honest conclusion

- **v2 asks the right question, but the data only supports a weak answer.** Knowing the last step and the scam type, any of the decent models names the scammer's next move about a third of the time and has it in its top 3 about two thirds of the time. The GRU is level with a per-type Markov table on every metric once fold noise is taken into account.
- **Payments dominate late in a script.** After a typical "contact → switch platform → fake proof → urgency" sequence the GRU's top move is `large_payment` for every scam type we tried, with very different confidence (pig_butchering about 0.93, the others 0.35–0.45). That matches the stories, but it means the model is mostly saying "they will ask for money next", which is useful as a warning but not very specific.
- **The model can still rank the current step highly** (for example `urgency_pressure` after `urgency_pressure`), because collapsed repeats are rare in training and the head does not learn to rule them out. The app's display filter, which drops steps already seen, is still worth keeping.
- **For the app today,** the per-type Markov table is as accurate and easier to explain. The GRU is a fair, honest v2 that gives both answers from one model; it is not an accuracy upgrade.

### Caveats

- **Small data:** only 554 next-move examples from 315 stories, and 277 known outcomes (101 losses). Fold-to-fold std is 0.02–0.10 overall and much larger for long prefixes, bigger than any gap between the GRU and the baselines.
- **Labels are LLM-extracted** from **self-reported** Reddit posts that lean toward dramatic cases. The models learn the extractor's habits as well as the scams.
- **The loss head is partly circular.** Payment steps are inputs, and you can't lose money without paying (§1: 92% of stories with a payment lost money). `victim_suspicious` is the same kind of near-outcome signal. A high AUC here does not mean the model foresees losses before the victim commits.
- **"No later move" is partly an artefact of storytelling.** Many posts stop when the victim gets suspicious, so the scripts we see are cut short, and late moves are under-represented.

### What more data would change

- With thousands of stories, especially in the small types (pig_butchering 17, romance 26), the per-type and second-order contexts would stop being sparse. That is where a GRU could start to pull ahead of count tables, since it shares strength across similar contexts.
- More known outcomes would allow an honest **pre-payment** evaluation of loss risk: train and test only on prefixes before any payment. That is the warning that matters, and it is too thin to measure with 277 outcomes.
- Richer inputs per step (the message text, amounts, platform) would give the sequence model something the Markov table can't use.
