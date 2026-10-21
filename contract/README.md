# Shared contract

Both sides build against these files. Change them only when both Arya and Faraj agree.

| File | What it defines |
|---|---|
| `vocab.json` | The fixed lists: steps, scam types, actors, outcomes, sources. Load enums from here in code. |
| `story.schema.json` | The stories file at `data/stories.json` (a JSON array of stories). |
| `matcher.schema.json` | What the matcher returns. |

## Stories file

- Path: `data/stories.json`. Arya produces it, Faraj consumes it.
- It starts as about 10 hand-written mock stories (ids `mock-*`) and is replaced in place by real stories at M1 and M2.
- Every field is always present. `amount_lost_usd` is `null` when the loss is unknown or there was none.
- `outcome` is `lost_money` if the victim lost any money, even if they later got out. Escapes still show up as a `victim_exits` event.

## Matcher

- `pipeline/matcher.py` exposes `match(text: str) -> dict`, and the dict conforms to `matcher.schema.json`.
- The API adds `loss_rate_if_continues` from the graphs file. That field is not part of the matcher's output.

## Graphs file

Faraj produces it and is the only consumer, so its shape isn't pinned here. See the "Graphs file" section of `PLAN.md`.
