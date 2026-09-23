"""Cross-source signal clustering into candidate Ventures.

Phase 4: the real differentiator over a single-source deal tracker. Groups
signals that are actually about the same underlying venture -- whether
that's the same company appearing across multiple grants (Mologic showing up
in three separate consortium projects this project has already seen live),
the same project appearing twice via two different funding mechanisms (the
"Horizon Europe Guarantee" duplicate pattern -- a UK participant in an EU
Horizon project shows up once via CORDIS and once via GtR under a different
grant label), or the same named person appearing across different signals.

Clustering is connected-components over a graph where two signals are linked
if they share a normalized organisation name OR a shared named person.
Deliberately favors precision over recall: a fuzzy name matcher would create
false merges (two unrelated "University of X" grants sharing nothing but the
institution), so organisation matching is exact-after-normalization, not
fuzzy, and a stoplist excludes common infrastructure/service partners (NPL,
national research centres, etc.) that show up across many unrelated CORDIS
consortiums and would otherwise wrongly merge them together.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from core.domain import is_company_org
from core.models import Signal, Venture

_COUNTRY_SUFFIX_RE = re.compile(r"\s*\([A-Z]{2,3}\)\s*$")
_TRAILING_PUNCT_RE = re.compile(r"[.,;:]+$")
_WHITESPACE_RE = re.compile(r"\s+")

ORG_STOPLIST = {
    "NPL MANAGEMENT LIMITED",
    "NATIONAL OCEANOGRAPHY CENTRE",
    "CENTRE NATIONAL DE LA RECHERCHE SCIENTIFIQUE CNRS",
    "FRAUNHOFER GESELLSCHAFT ZUR FORDERUNG DER ANGEWANDTEN FORSCHUNG EV",
    "KATHOLIEKE UNIVERSITEIT LEUVEN",
    "STICHTING IMEC NEDERLAND",
    "INSTITUT PASTEUR",
    "LONDON SCHOOL OF HYGIENE AND TROPICAL MEDICINE",
    "LONDON SCHOOL OF HYGIENE AND TROPICAL MEDICINE ROYAL CHARTER",
}

CLUSTER_SIZE_BONUS_PER_EXTRA_SIGNAL = 1.0
CLUSTER_SIZE_BONUS_CAP = 4.0


def normalize_org_name(name: str) -> str:
    if not name:
        return ""
    n = _COUNTRY_SUFFIX_RE.sub("", name)
    n = _TRAILING_PUNCT_RE.sub("", n)
    n = _WHITESPACE_RE.sub(" ", n).strip()
    return n.upper()


def normalize_person_name(name: str) -> str:
    if not name:
        return ""
    return _WHITESPACE_RE.sub(" ", name).strip().upper()


def _signal_org_keys(signal: Signal) -> set[str]:
    keys = set()
    for org in signal.organisations:
        key = normalize_org_name(org)
        if key and key not in ORG_STOPLIST:
            keys.add(key)
    return keys


def _signal_person_keys(signal: Signal) -> set[str]:
    keys = set()
    for person in signal.people:
        key = normalize_person_name(person)
        if key and key != "UNNAMED":
            keys.add(key)
    return keys


def _pick_venture_name(cluster_signals: list[Signal]) -> str:
    candidates: list[str] = []
    for sig in cluster_signals:
        candidates.extend(sig.organisations)

    company_candidates = [normalize_org_name(o) for o in candidates if is_company_org(o)]
    if company_candidates:
        return Counter(company_candidates).most_common(1)[0][0]

    all_normalized = [normalize_org_name(o) for o in candidates if o]
    if all_normalized:
        return Counter(all_normalized).most_common(1)[0][0]

    return cluster_signals[0].title[:60]


def _build_venture(cluster_signals: list[Signal]) -> Venture:
    name = _pick_venture_name(cluster_signals)
    base_score = max(s.score for s in cluster_signals)
    extra_signals = len(cluster_signals) - 1
    size_bonus = min(extra_signals * CLUSTER_SIZE_BONUS_PER_EXTRA_SIGNAL, CLUSTER_SIZE_BONUS_CAP)
    total_score = round(base_score + size_bonus, 2)

    reasons = [f"cluster of {len(cluster_signals)} signal(s), base score {base_score:.2f}"]
    if extra_signals > 0:
        reasons.append(f"cluster size bonus (+{size_bonus:.2f} for {extra_signals} additional signal(s))")
        sources = sorted({s.source for s in cluster_signals})
        if len(sources) > 1:
            reasons.append(f"appears across multiple sources: {', '.join(sources)}")

    return Venture(
        name=name,
        signals=sorted(cluster_signals, key=lambda s: s.score, reverse=True),
        score=total_score,
        score_reasons=reasons,
    )


def build_ventures(signals: list[Signal]) -> list[Venture]:
    n = len(signals)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    key_to_indices: dict[str, list[int]] = defaultdict(list)
    for idx, sig in enumerate(signals):
        for key in _signal_org_keys(sig):
            key_to_indices[f"org:{key}"].append(idx)
        for key in _signal_person_keys(sig):
            key_to_indices[f"person:{key}"].append(idx)

    for indices in key_to_indices.values():
        for a, b in zip(indices, indices[1:]):
            union(a, b)

    clusters: dict[int, list[int]] = defaultdict(list)
    for idx in range(n):
        clusters[find(idx)].append(idx)

    ventures = [_build_venture([signals[i] for i in indices]) for indices in clusters.values()]
    ventures.sort(key=lambda v: v.score, reverse=True)
    return ventures
