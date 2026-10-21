# Pipeline

scrape → clean → extract → `data/stories.json`

Run everything with `.venv/bin/python -m pipeline.<module>` from the repo root.

## Intermediate files (gitignored, local only)

### `data/raw/posts.jsonl`: written by `pipeline.scrape`

Each line is one post:

| field | type | notes |
|---|---|---|
| `id` | str | `reddit-<post id>` or `cfpb-<complaint id>`; unique |
| `source` | `"reddit"` or `"cfpb"` | |
| `source_id` | str | raw id from the source |
| `title` | str | `""` when the source has none |
| `body` | str | raw post text or complaint narrative, untouched |
| `created_utc` | int | unix seconds |
| `score` | int or null | Reddit score; null for CFPB |
| `url` | str | permalink, or CFPB complaint URL |
| `fetched_via` | str | which query or listing found it, e.g. `arctic:top`, `cfpb:pig butchering` |

### `data/clean/posts.jsonl`: written by `pipeline.clean`

Each line is one post that passed the filters:

| field | type | notes |
|---|---|---|
| `id` | str | same as raw |
| `source` | str | same as raw |
| `text` | str | cleaned title + blank line + cleaned body; markdown, links and PII removed |
| `n_chars` | int | `len(text)` |

`pipeline.clean` also prints a count of posts dropped at each filter.
