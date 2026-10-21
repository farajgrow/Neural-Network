# Findings

Generated from `analysis/graphs.json`. **Current data is the 10 hand-written mock stories (5 pig_butchering, 5 fake_job).** Rerun `python -m analysis.build` after the real stories land and update this file. Everything here is an association in self-reported data, not a cause.

## Clusters follow the path, not the scam type (mock data, n=10)

Clustering all stories by event sequence gives two clusters (silhouette 0.22), and they do not line up with the scam_type labels (purity 0.70, adjusted Rand index 0.06, roughly chance):

| Cluster | Label | Size | Scam types | Loss rate |
|---|---|---|---|---|
| c0 | pig_butchering · fake_proof_shown → small_payment | 6 | 4 pig_butchering, 2 fake_job | 1.00 (5 known) |
| c1 | fake_job · fake_proof_shown → victim_suspicious | 4 | 3 fake_job, 1 pig_butchering | 0.25 (4 known) |

The split is "fake proof, then a payment" versus "fake proof (or authority claim), then the victim gets suspicious". Both scam types appear in both clusters, so the shape of the script after the fake proof says more about where a story goes than what the scam is called.

Caveats:
- n=10 hand-written stories. This shows the method works, not that the pattern is real.
- `victim_suspicious` and `third_party_intervened` stay in the sequences (only `money_lost` / `victim_exits` are removed), and they sit close to the outcome. Some of the loss-rate gap between the clusters is circular for that reason.
- Self-reported stories skew toward people who lost money and posted about it.
