"""Cluster story step sequences into script variants and compare them with scam_type labels.

Pure standard library. Pipeline, run once on all stories and once within each scam type:
1. Each story becomes its collapsed step sequence (graphs.step_sequence) with the outcome
   steps removed, so clusters describe the script rather than how it ended.
2. Steps present in more than COMMON_SHARE of the group's stories (on the real data:
   initial_contact and victim_suspicious) are dropped from the distance, because they
   are in nearly every story and otherwise swamp the steps that tell scripts apart.
3. Pairwise distance = 0.75 * normalised edit distance + 0.25 * Jaccard distance over
   transitions (with start/end markers), on the reduced sequences.
4. Average-linkage agglomerative clustering via the nearest-neighbour chain algorithm (O(n^2)).
5. The dendrogram is cut at a k in 2..max_k: among cuts whose largest cluster holds at most
   MAX_CLUSTER_SHARE of the stories, the smallest k scoring within K_TOLERANCE of the best
   silhouette. Clusters smaller than `min_size` are pooled into one "outliers" group.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from analysis.graphs import OUTCOME_STEPS, PRECISION, load_vocab, loss_rate, step_sequence, story_outcome

EDIT_WEIGHT = 0.75
TRANSITION_WEIGHT = 0.25
MAX_K = 8
COMMON_SHARE = 0.7       # steps in more than this share of a group's stories are ignored
MAX_CLUSTER_SHARE = 0.7  # prefer cuts whose largest cluster holds at most this share
K_TOLERANCE = 0.9        # smallest k whose silhouette is >= this fraction of the best
MIN_TYPE_STORIES = 10    # per-type clustering needs at least this many stories
START, END = "^", "$"

METHOD = (
    "average-linkage agglomerative clustering on collapsed step sequences "
    "(outcome steps removed, steps in >70% of the group's stories ignored); "
    "distance = 0.75 * normalised edit distance + 0.25 * transition Jaccard distance; "
    "k = smallest k within 90% of the best silhouette among cuts whose largest cluster "
    "is <= 70% of stories; clusters below min_size pooled as outliers"
)

STEP_NAMES = {
    "initial_contact": "first contact",
    "platform_switch": "moves platform",
    "trust_building": "builds trust",
    "authority_claim": "claims authority",
    "urgency_pressure": "urgency",
    "fake_proof_shown": "fake proof",
    "small_payment": "small payment",
    "large_payment": "large payment",
    "credentials_shared": "credentials shared",
    "remote_access_granted": "remote access",
    "withdrawal_blocked": "withdrawal blocked",
    "victim_suspicious": "victim suspicious",
    "third_party_intervened": "third party steps in",
    "victim_exits": "victim exits",
    "money_lost": "money lost",
}


def step_name(step: str) -> str:
    """Short human-readable name for a step id."""
    return STEP_NAMES.get(step, step.replace("_", " "))


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, PRECISION)


# ---------------------------------------------------------------- distances

def edit_distance(a: list[str] | tuple, b: list[str] | tuple) -> int:
    """Levenshtein distance between two token sequences."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def normalised_edit_distance(a, b) -> float:
    """Edit distance divided by the longer length, in 0..1 (0 for two empty sequences)."""
    longest = max(len(a), len(b))
    return edit_distance(a, b) / longest if longest else 0.0


def transitions(sequence) -> frozenset[tuple[str, str]]:
    """Set of consecutive step pairs, including start and end markers."""
    padded = [START, *sequence, END]
    return frozenset(zip(padded, padded[1:]))


def sequence_distance(a, b) -> float:
    """Combined distance in 0..1 between two step sequences."""
    ta, tb = transitions(a), transitions(b)
    jaccard = 1.0 - len(ta & tb) / len(ta | tb)
    return EDIT_WEIGHT * normalised_edit_distance(a, b) + TRANSITION_WEIGHT * jaccard


def distance_matrix(sequences: list[tuple[str, ...]]) -> list[list[float]]:
    """Symmetric pairwise distance matrix; identical sequences are only compared once."""
    unique = sorted(set(sequences))
    index = {seq: i for i, seq in enumerate(unique)}
    u = len(unique)
    ud = [[0.0] * u for _ in range(u)]
    for i in range(u):
        for j in range(i + 1, u):
            ud[i][j] = ud[j][i] = sequence_distance(unique[i], unique[j])
    ids = [index[s] for s in sequences]
    return [[ud[i][j] for j in ids] for i in ids]


# ---------------------------------------------------------------- clustering

def average_linkage(dist: list[list[float]]) -> list[tuple[float, int, int]]:
    """Average-linkage merges (distance, a, b) via the nearest-neighbour chain, sorted by distance.

    a and b are point indices that belong to the two merged clusters. Ties go to the
    previous chain element, then to the lowest index, so the result is deterministic.
    """
    n = len(dist)
    d = [row[:] for row in dist]
    size = [1] * n
    alive = [True] * n
    merges: list[tuple[float, int, int]] = []
    chain: list[int] = []
    remaining = n

    while remaining > 1:
        if not chain:
            chain.append(next(i for i in range(n) if alive[i]))
        a = chain[-1]
        prev = chain[-2] if len(chain) > 1 else None
        best, best_d = -1, math.inf
        for j in range(n):
            if alive[j] and j != a and d[a][j] < best_d:
                best, best_d = j, d[a][j]
        if prev is not None and d[a][prev] <= best_d:
            best, best_d = prev, d[a][prev]
        if best != prev:
            chain.append(best)
            continue

        chain.pop()
        chain.pop()
        keep, drop = min(a, best), max(a, best)
        na, nb = size[a], size[best]
        for k in range(n):
            if alive[k] and k != a and k != best:
                value = (na * d[a][k] + nb * d[best][k]) / (na + nb)
                d[keep][k] = d[k][keep] = value
        alive[drop] = False
        size[keep] = na + nb
        merges.append((best_d, keep, drop))
        remaining -= 1

    return sorted(merges, key=lambda m: m[0])


def cut(merges: list[tuple[float, int, int]], n: int, k: int) -> list[int]:
    """Labels 0..k-1 from applying the n-k closest merges; labels follow first appearance."""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for _, a, b in merges[: max(n - k, 0)]:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    relabel: dict[int, int] = {}
    return [relabel.setdefault(find(i), len(relabel)) for i in range(n)]


def silhouette(dist: list[list[float]], labels: list[int]) -> float:
    """Mean silhouette score; singleton clusters score 0."""
    n = len(labels)
    members: dict[int, list[int]] = defaultdict(list)
    for i, label in enumerate(labels):
        members[label].append(i)
    if len(members) < 2:
        return 0.0
    total = 0.0
    for i in range(n):
        own = members[labels[i]]
        if len(own) == 1:
            continue
        a = sum(dist[i][j] for j in own) / (len(own) - 1)
        b = min(
            sum(dist[i][j] for j in group) / len(group)
            for label, group in members.items()
            if label != labels[i]
        )
        if max(a, b) > 0:
            total += (b - a) / max(a, b)
    return total / n


def choose_k(dist: list[list[float]], merges, max_k: int = MAX_K,
             min_size: int = 1) -> tuple[int, float | None]:
    """Pick k in 2..max_k.

    Candidates are cuts whose largest cluster holds at most MAX_CLUSTER_SHARE of the
    points and that have at least two clusters of min_size or more (all cuts if none
    qualify). Among them, the smallest k whose silhouette is within K_TOLERANCE of the
    best wins, so near-ties go to the simpler answer. k = 1 if nothing scores above 0.
    """
    n = len(dist)
    if n < 3:
        if n == 2 and dist[0][1] >= 0.5:
            return 2, None
        return min(n, 1), None
    scored = []
    for k in range(2, min(max_k, n - 1) + 1):
        labels = cut(merges, n, k)
        sizes = Counter(labels).values()
        balanced = max(sizes) <= MAX_CLUSTER_SHARE * n and sum(s >= min_size for s in sizes) >= 2
        scored.append((k, round(silhouette(dist, labels), 9), balanced))
    candidates = [c for c in scored if c[2]] or scored
    best = max(s for _, s, _ in candidates)
    if best <= 0:
        return 1, best
    k, s, _ = next(c for c in candidates if c[1] >= K_TOLERANCE * best - 1e-12)
    return k, s


def common_steps(sequences: list[tuple[str, ...]], share: float = COMMON_SHARE) -> frozenset[str]:
    """Steps that appear in more than `share` of the sequences."""
    if not sequences:
        return frozenset()
    counts = Counter(step for seq in sequences for step in set(seq))
    return frozenset(step for step, c in counts.items() if c / len(sequences) > share)


# ---------------------------------------------------------------- labels and metrics

def adjusted_rand_index(a: list, b: list) -> float | None:
    """Adjusted Rand index between two labelings of the same items."""
    n = len(a)
    if n < 2:
        return None
    comb = lambda x: x * (x - 1) / 2  # noqa: E731
    pairs = sum(comb(c) for c in Counter(zip(a, b)).values())
    sum_a = sum(comb(c) for c in Counter(a).values())
    sum_b = sum(comb(c) for c in Counter(b).values())
    expected = sum_a * sum_b / comb(n)
    maximum = (sum_a + sum_b) / 2
    if maximum == expected:
        return 1.0
    return (pairs - expected) / (maximum - expected)


def purity(clusters: list, labels: list) -> float | None:
    """Share of items whose label is the majority label of their cluster."""
    if not clusters:
        return None
    by_cluster: dict = defaultdict(Counter)
    for c, label in zip(clusters, labels):
        by_cluster[c][label] += 1
    return sum(max(counts.values()) for counts in by_cluster.values()) / len(clusters)


def _medoid(indices: list[int], dist, ids: list[str]) -> int:
    return min(indices, key=lambda i: (sum(dist[i][j] for j in indices), ids[i]))


def _prefix(type_counts: Counter, size: int) -> str:
    top_type, top_count = max(type_counts.items(), key=lambda kv: (kv[1], kv[0] == "other", kv[0]))
    return top_type if top_count / size >= 0.5 else "mixed"


def _label_steps(member_reduced, other_reduced, medoid_full, max_steps: int = 3) -> tuple[list[str], bool]:
    """Steps that characterise a cluster, in script order.

    Characteristic = present in at least half of the cluster's reduced sequences (failing
    that, in a quarter and more often than in other stories). If more
    than max_steps qualify, keep the ones most over-represented against the other stories.
    With none (the cluster is just the common steps) the medoid's full sequence is used,
    and the second value is True.
    """
    def share(seqs):
        counts = Counter(step for seq in seqs for step in set(seq))
        return {step: c / len(seqs) for step, c in counts.items()} if seqs else {}

    inside, outside = share(member_reduced), share(other_reduced)
    chosen = [step for step, rate in inside.items() if rate >= 0.5]
    if not chosen:  # a looser mix: the steps most over-represented among those in >= 25%
        chosen = [step for step, rate in inside.items()
                  if rate >= 0.25 and rate > outside.get(step, 0.0)]
    if not chosen:
        return list(medoid_full[:max_steps]), True
    chosen.sort(key=lambda st: (-(inside[st] - outside.get(st, 0.0)), -inside[st], st))
    chosen = chosen[:max_steps]
    positions: dict[str, list[float]] = defaultdict(list)
    for seq in member_reduced:
        for index, step in enumerate(seq):
            positions[step].append(index)
    return sorted(chosen, key=lambda st: (sum(positions[st]) / len(positions[st]), st)), False


def _format_label(prefix: str, steps: list[str], only_common: bool) -> str:
    if not steps:
        return f"{prefix} · no script steps"
    text = " → ".join(step_name(st) for st in steps)
    return f"{prefix} · {text}{' only' if only_common else ''}"


def _edge_counts(full_sequences) -> list[dict]:
    edges: Counter = Counter()
    for seq in full_sequences:
        edges.update(zip(seq, seq[1:]))
    return [{"from": a, "to": b, "count": c} for (a, b), c in sorted(edges.items(), key=lambda e: (-e[1], e[0]))]


# ---------------------------------------------------------------- entry point

def _empty_section(n: int, max_k: int, min_size: int) -> dict:
    return {
        "method": METHOD,
        "params": {"edit_weight": EDIT_WEIGHT, "transition_weight": TRANSITION_WEIGHT,
                   "max_k": max_k, "min_size": min_size, "k": 0, "silhouette": None,
                   "common_share": COMMON_SHARE, "max_cluster_share": MAX_CLUSTER_SHARE,
                   "ignored_steps": []},
        "assignments": {},
        "clusters": [],
        "agreement": {"n": n, "purity": None, "inverse_purity": None, "adjusted_rand": None},
    }


def _cluster_group(ids, sequences, full_sequences, types, outcomes, max_k: int) -> dict:
    """Cluster one group of stories (already deduplicated and sorted by id)."""
    n = len(ids)
    min_size = max(3, math.ceil(0.02 * n))
    result = _empty_section(n, max_k, min_size)
    if n == 0:
        return result

    ignored = common_steps(sequences)
    reduced = [tuple(st for st in seq if st not in ignored) for seq in sequences]
    dist = distance_matrix(reduced)
    merges = average_linkage(dist)
    k, score = choose_k(dist, merges, max_k, min_size)
    raw = cut(merges, n, max(k, 1))

    groups: dict[int, list[int]] = defaultdict(list)
    for i, label in enumerate(raw):
        groups[label].append(i)
    small = lambda g: len(g) < min_size and len(groups) > 1  # noqa: E731
    regular = [g for g in groups.values() if not small(g)]
    outliers = sorted(i for g in groups.values() if small(g) for i in g)
    if not regular:  # every cluster is tiny: keep them all as one group
        regular, outliers = [sorted(range(n))], []
    regular.sort(key=lambda g: (-len(g), ids[g[0]]))
    ordered = [(g, False) for g in regular] + ([(outliers, True)] if outliers else [])

    final = [""] * n
    label_parts = []
    for index, (members, is_outlier) in enumerate(ordered):
        cid = f"c{index}"
        for i in members:
            final[i] = cid
        type_counts = Counter(types[i] for i in members)
        medoid = _medoid(members, dist, ids)
        member_set = set(members)
        others = [reduced[i] for i in range(n) if i not in member_set]
        steps, only_common = _label_steps([reduced[i] for i in members], others, sequences[medoid])
        label_parts.append((_prefix(type_counts, len(members)), steps, only_common, is_outlier,
                            reduced[medoid]))
        known = [outcomes[i] for i in members if outcomes[i] != "unknown"]
        result["clusters"].append({
            "id": cid,
            "label": "",
            "size": len(members),
            "loss_rate": _round(loss_rate([outcomes[i] for i in members])),
            "n_known": len(known),
            "scam_type_counts": dict(sorted(type_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "typical_sequence": list(sequences[medoid]),
            "medoid_id": ids[medoid],
            "outlier": is_outlier,
            "label_steps": steps,
            "edges": _edge_counts(full_sequences[i] for i in members),
            "story_ids": [ids[i] for i in members],
        })

    _assign_labels(result["clusters"], label_parts)
    result["assignments"] = dict(zip(ids, final))
    result["params"].update(k=len(ordered), silhouette=_round(score), ignored_steps=sorted(ignored))
    result["agreement"].update(
        purity=_round(purity(final, types)),
        inverse_purity=_round(purity(types, final)),
        adjusted_rand=_round(adjusted_rand_index(final, types)),
    )
    return result


def _assign_labels(clusters: list[dict], parts) -> None:
    """Readable, distinct labels: '<type> · step → step'. Clashes get the medoid's full script, then a number."""
    labels = [("outliers" if out else _format_label(p, st, oc)) for p, st, oc, out, _ in parts]
    counts = Counter(labels)
    for i, (prefix, steps, only_common, is_outlier, medoid_reduced) in enumerate(parts):
        if counts[labels[i]] > 1 and not is_outlier and medoid_reduced and list(medoid_reduced) != steps:
            labels[i] = _format_label(prefix, list(medoid_reduced[:4]), False)
    seen: Counter = Counter()
    counts = Counter(labels)
    for i, label in enumerate(labels):
        if counts[label] > 1:
            seen[label] += 1
            if seen[label] > 1:
                labels[i] = f"{label} (variant {seen[label]})"
    for cluster, label in zip(clusters, labels):
        cluster["label"] = label


def _prepare(stories: list[dict], vocab: dict):
    valid_steps = frozenset(vocab["steps"])
    script_steps = valid_steps - OUTCOME_STEPS
    valid_outcomes = frozenset(vocab["outcomes"])
    valid_types = frozenset(vocab["scam_types"])
    seen: dict[str, dict] = {}
    for story in stories:
        sid = story.get("id")
        if isinstance(sid, str) and sid not in seen:
            seen[sid] = story
    ids = sorted(seen)
    return (
        ids,
        [tuple(step_sequence(seen[i], script_steps)) for i in ids],
        [tuple(step_sequence(seen[i], valid_steps)) for i in ids],
        [seen[i].get("scam_type") if seen[i].get("scam_type") in valid_types else "other" for i in ids],
        [story_outcome(seen[i], valid_outcomes) for i in ids],
    )


def build_clusters(stories: list[dict], vocab: dict | None = None, max_k: int = MAX_K) -> dict:
    """Cluster all stories together (cross-type view) and return the `clusters` section."""
    vocab = vocab or load_vocab()
    return _cluster_group(*_prepare(stories, vocab), max_k)


def build_clusters_by_type(stories: list[dict], vocab: dict | None = None, max_k: int = MAX_K) -> dict:
    """Cluster within each scam type: {scam_type: <same shape as build_clusters>}.

    Types with fewer than MIN_TYPE_STORIES stories are left out.
    """
    vocab = vocab or load_vocab()
    ids, seqs, full, types, outcomes = _prepare(stories, vocab)
    by_type: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(types):
        by_type[t].append(i)
    order = sorted(by_type, key=lambda t: (-len(by_type[t]), vocab["scam_types"].index(t)))
    return {
        t: _cluster_group([ids[i] for i in by_type[t]], [seqs[i] for i in by_type[t]],
                          [full[i] for i in by_type[t]], [types[i] for i in by_type[t]],
                          [outcomes[i] for i in by_type[t]], max_k)
        for t in order
        if len(by_type[t]) >= MIN_TYPE_STORIES
    }


def summarize(section: dict | None, name: str = "clusters") -> str:
    """Readable cluster summary for the build script."""
    if not section:
        return f"{name}: skipped"
    agreement = section["agreement"]
    lines = [
        f"{name}: n={agreement['n']} k={section['params']['k']} ignored={section['params'].get('ignored_steps')} "
        f" silhouette={section['params']['silhouette']} "
        f"purity={agreement['purity']} inverse_purity={agreement['inverse_purity']} "
        f"ARI={agreement['adjusted_rand']}"
    ]
    for c in section["clusters"]:
        lines.append(f"  {c['id']:<4} n={c['size']:<4} loss={c['loss_rate']} (known {c.get('n_known')}) {c['label']}  {c['scam_type_counts']}")
    return "\n".join(lines)
