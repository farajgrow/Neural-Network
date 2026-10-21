# Analysis

Turns `data/stories.json` into `analysis/graphs.json`, which the API serves to the UI.

```bash
python -m analysis.build            # reads data/stories.json, writes analysis/graphs.json
python -m pytest analysis/tests
```

Standard library only, including clustering. `python -m analysis.build --no-clusters` skips clustering (`clusters` and `clusters_by_type` stay null).

## Definitions

- **Node loss rate**: of the stories that contain this step, the share with `outcome == "lost_money"`. Stories with `outcome == "unknown"` are left out of the denominator. `null` if no known-outcome stories pass through.
- **Edge**: consecutive events in a story, `events[i].step -> events[i+1].step`. Repeated identical consecutive steps are collapsed first. A story that contains a step more than once counts once for that node.
- **Minimum support**: `min_support = max(3, ceil(5% of the graph's stories))`, stored per graph. A step needs `count >= min_support` to be a pinch or escape candidate, so a step seen in 3 of 17 stories cannot headline a graph.
- **Pinch point**: a step where the loss rate jumps compared with the steps that lead into it. `jump = loss_rate(step) - weighted mean loss_rate(predecessors)`, weighted by edge count. Candidates need `count >= min_support` and at least 3 known-outcome stories. Ranked by `score`: the same jump after shrinking every loss rate toward the graph's overall loss rate with 5 pseudo-stories (`(lost + 5 × overall) / (known + 5)`), so 3-for-3 does not beat 41-of-47. Outcome steps (`money_lost`, `victim_exits`) are never pinch points. Top 3 with positive `jump` and `score`. Nothing is hard-coded about which steps are victim commitments; on the real data payments win on their own.
- **Escape point**: a step most often followed directly by `victim_exits`. Candidates need `count >= min_support` and at least 2 exits; outcome steps (`money_lost`, `victim_exits`) are excluded. Ranked by `exit_count`, ties broken by `exit_rate = exit_count / count`. Top 3.
- **Edge loss rate**: of the known-outcome stories that take the transition at least once, the share that lost money (`n_known` is that denominator). `count` still counts every occurrence.
- **avg_position**: mean of `index / (len(events) - 1)` for the step across stories, in 0..1. The UI uses it for left-to-right layout.

- **Clusters** (`analysis/clusters.py`): run twice, on all stories together (`clusters`, the cross-type view) and within each scam type with at least 10 stories (`clusters_by_type`). Each story becomes its collapsed step sequence with `money_lost` / `victim_exits` removed, so clusters describe the script, not the ending. Steps present in more than 70% of the group's stories are then ignored for the distance (`params.ignored_steps`; on the real data `initial_contact` and `victim_suspicious`), because they are in nearly every story and otherwise collapse everything into one cluster. Distance = 0.75 × edit distance / longer length + 0.25 × Jaccard distance over transitions (with start/end markers), on the reduced sequences. Average-linkage agglomerative clustering (nearest-neighbour chain, O(n²)). The dendrogram is cut at k in 2..8: among cuts whose largest cluster holds at most 70% of the stories and that have at least two clusters of `min_size`, the smallest k whose silhouette is at least 90% of the best (fewer, bigger variants win near-ties); k = 1 if no k scores above 0. Clusters smaller than `min_size = max(3, ceil(2% of n))` are pooled into one `outliers` cluster, listed last. Ids `c0..` by size. `typical_sequence` is the medoid's (full, outcome-free) sequence. Labels are `<majority scam_type or "mixed"> · <characteristic steps>`: up to 3 non-ignored steps found in at least half the cluster's stories (failing that, in a quarter and more often than elsewhere), in script order, using short step names (`fake proof`, `small payment`, ...). A cluster made only of the ignored steps is labelled with the medoid's sequence plus `only`, e.g. `fake_job · first contact → victim suspicious only`. Clashing labels fall back to the medoid's script, then ` (variant 2)`. Output is deterministic and independent of input order. Well under 1 s for 500 stories.
- **Agreement**: `purity` (share of stories matching their cluster's majority scam_type), `inverse_purity` (share of stories in their scam_type's majority cluster; low means a type splits into variants), `adjusted_rand` (ARI vs scam_type; 0 ≈ chance, 1 = identical).

These are associations in self-reported data, not causes.

## `analysis/graphs.json`

```jsonc
{
  "generated_at": "2026-10-21T15:00:00Z",
  "n_stories": 10,
  "scam_types": ["pig_butchering", "fake_job"],   // types with at least 1 story, biggest first
  "graphs": {
    // one entry per type in scam_types, plus "all" (every story combined)
    "pig_butchering": {
      "scam_type": "pig_butchering",
      "n_stories": 5,
      "loss_rate": 0.75,                 // over known-outcome stories, null if none
      "outcome_counts": {"lost_money": 3, "escaped": 1, "unknown": 1},
      "nodes": [
        {"step": "small_payment", "count": 3, "loss_rate": 1.0, "n_known": 3, "avg_position": 0.55}
      ],
      "edges": [
        {"from": "fake_proof_shown", "to": "small_payment", "count": 3,
         "loss_rate": 1.0, "n_known": 3}   // loss_rate / n_known: known-outcome stories taking this edge
      ],
      "min_support": 3,                    // count needed to be a pinch / escape candidate
      "pinch_points": [
        {"step": "small_payment", "loss_rate": 1.0, "prev_loss_rate": 0.75, "jump": 0.25,
         "n_known": 3, "score": 0.12}    // score = shrunk jump, used for ranking
      ],
      "escape_points": [
        {"step": "third_party_intervened", "exit_count": 1, "exit_rate": 1.0}
      ],
      "step_to_story_ids": {"small_payment": ["mock-001", "mock-002", "mock-004"]}
    }
  },
  // Added by F3 (sequence clustering). Absent or null until then.
  "clusters": {
    "method": "string describing the method",
    "params": {"edit_weight": 0.75, "transition_weight": 0.25, "max_k": 8, "min_size": 3, "k": 2, "silhouette": 0.22,
               "common_share": 0.7, "max_cluster_share": 0.7, "ignored_steps": ["initial_contact"]},
    "assignments": {"mock-001": "c0"},
    "clusters": [
      {"id": "c0", "label": "pig_butchering · fake proof → small payment", "size": 3, "loss_rate": 0.66,
       "n_known": 3,                                   // denominator of loss_rate
       "scam_type_counts": {"pig_butchering": 3}, "typical_sequence": ["initial_contact", "..."],
       "medoid_id": "mock-001", "outlier": false,
       "label_steps": ["fake_proof_shown", "small_payment"],   // step ids behind the label
       "edges": [{"from": "fake_proof_shown", "to": "small_payment", "count": 3}],  // transitions this
       // cluster's stories use (outcome steps included, so they match graph edges)
       "story_ids": ["mock-001", "..."]}
    ],
    "agreement": {"n": 10, "purity": 0.7, "inverse_purity": 0.7, "adjusted_rand": 0.06}
  },
  // Same shape as "clusters", computed within each scam type that has >= 10 stories.
  // Keys follow scam_types order. Purity is trivially 1 inside a type.
  "clusters_by_type": {
    "pig_butchering": {"method": "...", "params": {}, "assignments": {}, "clusters": [], "agreement": {}}
  }
}
```

## Neural network (`analysis/model/`)

A small GRU (`gru-v2`) that predicts the **scammer's next move** and the **probability of losing money** from the steps seen so far plus the scam type. It is cross-validated against Markov and logistic-regression baselines. Results and an honest read are in `findings.md` under "Neural network (v2: next scam move)". In short, it ties the baselines.

This is the only part of `analysis/` that needs third-party packages. The rest stays standard library only.

**Training needs torch; inference needs only numpy.** `predict()` runs a pure-numpy copy of the GRU's forward pass (`numpy_gru.py`) on `gru_weights.npz`, so the app can deploy without torch (for example on Vercel). `train.py` writes `gru_weights.npz` right after `gru.pt`, so they stay in sync. To regenerate it from an existing `gru.pt` (needs torch):

```bash
.venv/bin/python -m analysis.model.export_weights   # gru.pt -> gru_weights.npz
```

```bash
python3 -m venv .venv
.venv/bin/pip install -r analysis/requirements.txt -r app/requirements.txt pytest
.venv/bin/python -m analysis.model.train     # 5-fold CV + final model, about 7 s on CPU
.venv/bin/python -m pytest analysis/tests    # torch tests are skipped if torch is missing; predict() tests still run
```

```python
from analysis.model.predict import predict
predict(["initial_contact", "platform_switch"], "pig_butchering")
# {"next_step_probs": {...11 scammer moves, sums to 1...}, "top_next": "trust_building", "p_loss": 0.2}
```

| File | What it is |
|---|---|
| `data.py` | Stories → step sequences (outcome steps removed) → prefix examples (`next_step_examples` for the move head, `loss_examples` for the loss head). Also builds the 5-fold story-level split, stratified by scam type. |
| `baselines.py` | Most-frequent next move, Markov (order 1/2, optionally per scam type, with backoff), base-rate and logistic-regression loss models. |
| `gru.py` | The PyTorch model (step and type embeddings → 1-layer GRU(32) → two heads), masked training loss, and early stopping. |
| `metrics.py` | Top-k accuracy, top-1 among moves not already seen, log loss, ROC AUC, Brier score and calibration bins, in numpy. |
| `train.py` | Runs the CV, prints the table, and writes `results.json`. Then trains on all stories and writes `gru.pt` and `model_card.json`. |
| `predict.py` | `predict(steps, scam_type)` using `gru_weights.npz` and numpy only. `load_model()` still loads `gru.pt` with torch. |
| `numpy_gru.py` | The GRU forward pass (PyTorch gate order r, z, n) and both heads in numpy. A test checks it matches torch to 1e-5. |
| `export_weights.py` | Writes `gru.pt`'s tensors, vocabulary and config to `gru_weights.npz` (about 25 KB). |
| `learning_curve.py` | Retrains every model on 25/50/75/100% of each fold's training stories and writes `learning_curve.json` (about 10 s). |
| `results.json`, `gru.pt`, `gru_weights.npz`, `model_card.json` | Committed outputs (each model file is under 30 KB), so the app can load it without retraining. |

Definitions:

- **Inputs** are the collapsed steps with `money_lost` and `victim_exits` removed. Repeats created by that removal are collapsed again. The model never sees the outcome. The reactions `victim_suspicious` and `third_party_intervened` **are kept as inputs**, because they carry signal (especially for loss risk); they are just never targets.
- **Next-step targets (v2)** are the scammer's **moves**: the vocabulary minus the reactions (`victim_suspicious`, `third_party_intervened`, `victim_exits`) and minus `money_lost`, 11 steps (the matcher's script steps minus `money_lost`, defined in `data.py`). For each prefix the target is the next move later in the story, skipping any reactions in between. A prefix with no later move gives no next-step example, and there is no `END` class. (v1 predicted the very next step or `END`; `victim_suspicious` was 331 of 953 such transitions, so v1 mostly predicted that.)
- **Loss label:** 1 = `lost_money`, 0 = `escaped`, applied to every prefix of the story, including prefixes with no later move, so the loss head uses the same examples as v1. `unknown` stories train only the next-step head, because the loss head's loss is masked for them.
- **Fair comparison:** the GRU's early-stopping split (15% of the training stories) is also used to pick each baseline's single setting. Test folds are only used for scoring. Runs are seeded and deterministic.
