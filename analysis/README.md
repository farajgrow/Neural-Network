# Analysis

Turns `data/stories.json` into `analysis/graphs.json`, which the API serves to the UI.

```bash
python -m analysis.build            # reads data/stories.json, writes analysis/graphs.json
python -m pytest analysis/tests
```

Standard library only, including clustering. `python -m analysis.build --no-clusters` skips clustering.

## Definitions

- **Node loss rate**: of the stories that contain this step, the share with `outcome == "lost_money"`. Stories with `outcome == "unknown"` are left out of the denominator. `null` if no known-outcome stories pass through.
- **Edge**: consecutive events in a story, `events[i].step -> events[i+1].step`. Repeated identical consecutive steps are collapsed first. A story that contains a step more than once counts once for that node.
- **Pinch point**: a step where the loss rate jumps compared with the steps that lead into it. `jump = loss_rate(step) - weighted mean loss_rate(predecessors)`, weighted by edge count. Outcome steps (`money_lost`, `victim_exits`) are never pinch points. Top 3 by jump, positive jumps only.
- **Escape point**: a step most often followed directly by `victim_exits`. Ranked by `exit_count`, ties broken by `exit_rate = exit_count / count`. `victim_exits` itself is excluded. Top 3.
- **avg_position**: mean of `index / (len(events) - 1)` for the step across stories, in 0..1. The UI uses it for left-to-right layout.

- **Clusters** (`analysis/clusters.py`): every story (across all scam types) becomes its collapsed step sequence with `money_lost` / `victim_exits` removed, so clusters describe the script, not the ending. Distance = 0.75 × edit distance / longer length + 0.25 × Jaccard distance over transitions (with start/end markers). Average-linkage agglomerative clustering (nearest-neighbour chain, O(n²)); the dendrogram is cut at the k in 2..15 with the best silhouette (k = 1 if no k scores above 0). Clusters smaller than `min_size = max(2, ceil(2% of n))` are pooled into one `outliers` cluster, listed last. Ids `c0..` by size. `typical_sequence` is the medoid's sequence. Labels are `<majority scam_type or "mixed"> · <transition most over-represented vs other stories>`. Output is deterministic and independent of input order. About 1 s for 500 stories.
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
        {"step": "small_payment", "count": 3, "loss_rate": 1.0, "avg_position": 0.55}
      ],
      "edges": [
        {"from": "fake_proof_shown", "to": "small_payment", "count": 3}
      ],
      "pinch_points": [
        {"step": "small_payment", "loss_rate": 1.0, "prev_loss_rate": 0.75, "jump": 0.25}
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
    "params": {"edit_weight": 0.75, "transition_weight": 0.25, "max_k": 15, "min_size": 2, "k": 2, "silhouette": 0.22},
    "assignments": {"mock-001": "c0"},
    "clusters": [
      {"id": "c0", "label": "short human label", "size": 3, "loss_rate": 0.66,
       "scam_type_counts": {"pig_butchering": 3}, "typical_sequence": ["initial_contact", "..."],
       "medoid_id": "mock-001", "outlier": false, "story_ids": ["mock-001", "..."]}
    ],
    "agreement": {"n": 10, "purity": 0.7, "inverse_purity": 0.7, "adjusted_rand": 0.06}
  }
}
```
