# Analysis

Turns `data/stories.json` into `analysis/graphs.json`, which the API serves to the UI.

```bash
python -m analysis.build            # reads data/stories.json, writes analysis/graphs.json
python -m pytest analysis/tests
```

Standard library only for graph construction. Clustering (F3) may add numpy / scikit-learn.

## Definitions

- **Node loss rate**: of the stories that contain this step, the share with `outcome == "lost_money"`. Stories with `outcome == "unknown"` are left out of the denominator. `null` if no known-outcome stories pass through.
- **Edge**: consecutive events in a story, `events[i].step -> events[i+1].step`. Repeated identical consecutive steps are collapsed first. A story that contains a step more than once counts once for that node.
- **Pinch point**: a step where the loss rate jumps compared with the steps that lead into it. `jump = loss_rate(step) - weighted mean loss_rate(predecessors)`, weighted by edge count. Outcome steps (`money_lost`, `victim_exits`) are never pinch points. Top 3 by jump, positive jumps only.
- **Escape point**: a step most often followed directly by `victim_exits`. Ranked by `exit_count`, ties broken by `exit_rate = exit_count / count`. `victim_exits` itself is excluded. Top 3.
- **avg_position**: mean of `index / (len(events) - 1)` for the step across stories, in 0..1. The UI uses it for left-to-right layout.

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
    "assignments": {"mock-001": "c0"},
    "clusters": [
      {"id": "c0", "label": "short human label", "size": 3, "loss_rate": 0.66,
       "scam_type_counts": {"pig_butchering": 3}, "typical_sequence": ["initial_contact", "..."]}
    ]
  }
}
```
