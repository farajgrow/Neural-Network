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
