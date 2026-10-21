"""Cluster story step sequences into script variants and compare them with scam_type labels.

Pure standard library. Pipeline:
1. Each story becomes its collapsed step sequence (graphs.step_sequence) with the outcome
   steps removed, so clusters describe the script rather than how it ended.
2. Pairwise distance = 0.75 * normalised edit distance + 0.25 * Jaccard distance over
   transitions (with start/end markers).
3. Average-linkage agglomerative clustering via the nearest-neighbour chain algorithm (O(n^2)).
4. The dendrogram is cut at the k with the best silhouette score; clusters smaller than
   `min_size` are pooled into one "outliers" group.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from analysis.graphs import OUTCOME_STEPS, PRECISION, load_vocab, loss_rate, step_sequence, story_outcome

EDIT_WEIGHT = 0.75
TRANSITION_WEIGHT = 0.25
MAX_K = 15
START, END = "^", "$"

METHOD = (
    "average-linkage agglomerative clustering on collapsed step sequences "
    "(outcome steps removed); distance = 0.75 * normalised edit distance "
    "+ 0.25 * transition Jaccard distance; k chosen by silhouette; "
    "clusters below min_size pooled as outliers"
)


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


def choose_k(dist: list[list[float]], merges, max_k: int = MAX_K) -> tuple[int, float | None]:
    """Pick the k in 2..max_k with the best silhouette (smaller k wins ties)."""
    n = len(dist)
    if n < 3:
        if n == 2 and dist[0][1] >= 0.5:
            return 2, None
        return min(n, 1), None
    best_k, best_s = 1, None
    for k in range(2, min(max_k, n - 1) + 1):
        s = round(silhouette(dist, cut(merges, n, k)), 9)
        if best_s is None or s > best_s:
            best_k, best_s = k, s
    if best_s is not None and best_s <= 0:
        return 1, best_s
    return best_k, best_s


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


def _label(member_seqs, other_seqs, type_counts: Counter, size: int, medoid_seq) -> str:
    """Short label: dominant scam type plus the most distinguishing transition (or step)."""
    top_type, top_count = max(type_counts.items(), key=lambda kv: (kv[1], kv[0] == "other", kv[0]))
    prefix = top_type if top_count / size >= 0.5 else "mixed"

    def share(seqs, feature_fn):
        counts = Counter(f for s in seqs for f in set(feature_fn(s)))
        return {f: c / len(seqs) for f, c in counts.items()} if seqs else {}

    pairs = lambda s: list(zip(s, s[1:]))  # noqa: E731
    inside, outside = share(member_seqs, pairs), share(other_seqs, pairs)
    medoid_pairs = pairs(medoid_seq)
    scored = sorted(
        (-(rate - outside.get(t, 0.0)), -rate, medoid_pairs.index(t) if t in medoid_pairs else 99, t)
        for t, rate in inside.items()
        if rate >= 0.5
    )
    if scored:
        a, b = scored[0][3]
        return f"{prefix} · {a} → {b}"
    steps_in, steps_out = share(member_seqs, list), share(other_seqs, list)
    ranked = sorted((-(r - steps_out.get(s, 0.0)), s) for s, r in steps_in.items())
    return f"{prefix} · {ranked[0][1]}" if ranked else f"{prefix} · no script steps"


# ---------------------------------------------------------------- entry point

def build_clusters(stories: list[dict], vocab: dict | None = None, max_k: int = MAX_K) -> dict:
    """Cluster all stories and return the `clusters` section of the graphs file."""
    vocab = vocab or load_vocab()
    valid_steps = frozenset(vocab["steps"]) - OUTCOME_STEPS
    valid_outcomes = frozenset(vocab["outcomes"])
    valid_types = frozenset(vocab["scam_types"])

    seen: dict[str, dict] = {}
    for story in stories:
        sid = story.get("id")
        if isinstance(sid, str) and sid not in seen:
            seen[sid] = story
    ids = sorted(seen)
    n = len(ids)
    sequences = [tuple(step_sequence(seen[i], valid_steps)) for i in ids]
    types = [seen[i].get("scam_type") if seen[i].get("scam_type") in valid_types else "other" for i in ids]
    outcomes = [story_outcome(seen[i], valid_outcomes) for i in ids]
    min_size = max(2, math.ceil(0.02 * n))

    result = {
        "method": METHOD,
        "params": {"edit_weight": EDIT_WEIGHT, "transition_weight": TRANSITION_WEIGHT,
                   "max_k": max_k, "min_size": min_size, "k": 0, "silhouette": None},
        "assignments": {},
        "clusters": [],
        "agreement": {"n": n, "purity": None, "inverse_purity": None, "adjusted_rand": None},
    }
    if n == 0:
        return result

    dist = distance_matrix(sequences)
    merges = average_linkage(dist)
    k, score = choose_k(dist, merges, max_k)
    raw = cut(merges, n, max(k, 1))

    groups: dict[int, list[int]] = defaultdict(list)
    for i, label in enumerate(raw):
        groups[label].append(i)
    small = lambda g: len(g) < min_size and len(groups) > 1  # noqa: E731
    regular = [g for g in groups.values() if not small(g)]
    outliers = sorted(i for g in groups.values() if small(g) for i in g)
    regular.sort(key=lambda g: (-len(g), ids[g[0]]))
    ordered = [(g, False) for g in regular] + ([(outliers, True)] if outliers else [])

    final = [""] * n
    for index, (members, is_outlier) in enumerate(ordered):
        cid = f"c{index}"
        for i in members:
            final[i] = cid
        type_counts = Counter(types[i] for i in members)
        medoid = _medoid(members, dist, ids)
        member_set = set(members)
        others = [sequences[i] for i in range(n) if i not in member_set]
        label = "outliers" if is_outlier else _label(
            [sequences[i] for i in members], others, type_counts, len(members), sequences[medoid])
        result["clusters"].append({
            "id": cid,
            "label": label,
            "size": len(members),
            "loss_rate": _round(loss_rate([outcomes[i] for i in members])),
            "scam_type_counts": dict(sorted(type_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "typical_sequence": list(sequences[medoid]),
            "medoid_id": ids[medoid],
            "outlier": is_outlier,
            "story_ids": [ids[i] for i in members],
        })

    _dedupe_labels(result["clusters"])
    result["assignments"] = dict(zip(ids, final))
    result["params"].update(k=len(ordered), silhouette=_round(score))
    result["agreement"].update(
        purity=_round(purity(final, types)),
        inverse_purity=_round(purity(types, final)),
        adjusted_rand=_round(adjusted_rand_index(final, types)),
    )
    return result


def _dedupe_labels(clusters: list[dict]) -> None:
    """Append the cluster id to any label that is not unique."""
    counts = Counter(c["label"] for c in clusters)
    for c in clusters:
        if counts[c["label"]] > 1:
            c["label"] = f"{c['label']} ({c['id']})"


def summarize(section: dict | None) -> str:
    """Readable cluster summary for the build script."""
    if not section:
        return "clusters: skipped"
    agreement = section["agreement"]
    lines = [
        f"clusters: k={section['params']['k']} silhouette={section['params']['silhouette']} "
        f"purity={agreement['purity']} inverse_purity={agreement['inverse_purity']} "
        f"ARI={agreement['adjusted_rand']}"
    ]
    for c in section["clusters"]:
        lines.append(f"  {c['id']:<4} n={c['size']:<4} loss={c['loss_rate']} {c['label']}  {c['scam_type_counts']}")
    return "\n".join(lines)
