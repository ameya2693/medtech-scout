"""Cross-source signal clustering into candidate Ventures.

Signals are linked when they share a COMPANY organisation or a person.
Lesson learned: linking on any organisation let hub nodes (e.g. University of
Leeds) chain 113 unrelated signals into one mega-cluster ("MOLOGIC LTD").
Fixes: (1) org linking is restricted to company orgs; (2) any link key that
touches more than MAX_SIGNALS_PER_CLUSTER_KEY signals is treated as a hub and
skipped (with a warning).
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict

from core.domain import is_company_org
from core.models import Signal, Venture

logger = logging.getLogger(__name__)

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

MAX_SIGNALS_PER_CLUSTER_KEY = 6
CLUSTER_SIZE_BONUS_PER_EXTRA_SIGNAL = 1.0
CLUSTER_SIZE_BONUS_CAP = 4.0


def normalize_org_name(name: str) -> str:
    n = _COUNTRY_SUFFIX_RE.sub("", name or "")
    n = _TRAILING_PUNCT_RE.sub("", n.strip())
    return _WHITESPACE_RE.sub(" ", n).upper()


def normalize_person_name(name: str) -> str:
    return _WHITESPACE_RE.sub(" ", (name or "").strip()).upper()


def _signal_org_keys(signal: Signal) -> set[str]:
    keys = set()
    for org in signal.organisations or []:
        norm = normalize_org_name(org)
        if not norm or not is_company_org(norm):
            continue
        if norm not in ORG_STOPLIST:
            keys.add(f"org:{norm}")
    return keys


def _signal_person_keys(signal: Signal) -> set[str]:
    keys = set()
    for p in signal.people or []:
        norm = normalize_person_name(p)
        if norm and "UNNAMED" not in norm:
            keys.add(f"person:{norm}")
    return keys


def _pick_venture_name(cluster_signals: list[Signal]) -> str:
    companies = Counter()
    others = Counter()
    for s in cluster_signals:
        for org in s.organisations or []:
            norm = normalize_org_name(org)
            if not norm:
                continue
            (companies if is_company_org(norm) and norm not in ORG_STOPLIST else others)[norm] += 1
    if companies:
        return companies.most_common(1)[0][0]
    if others:
        return others.most_common(1)[0][0]
    return (cluster_signals[0].title or "Unnamed")[:60]


def _build_venture(cluster_signals: list[Signal]) -> Venture:
    best = max(s.score for s in cluster_signals)
    extra = len(cluster_signals) - 1
    bonus = min(extra * CLUSTER_SIZE_BONUS_PER_EXTRA_SIGNAL, CLUSTER_SIZE_BONUS_CAP)
    reasons = []
    if extra:
        reasons.append(f"+{bonus:.1f} clustered: {len(cluster_signals)} signals")
        sources = sorted({s.source for s in cluster_signals})
        if len(sources) > 1:
            reasons.append(f"multi-source ({', '.join(sources)})")
    return Venture(
        name=_pick_venture_name(cluster_signals),
        signals=sorted(cluster_signals, key=lambda s: s.score, reverse=True),
        score=best + bonus,
        score_reasons=reasons,
    )


def build_ventures(signals: list[Signal]) -> list[Venture]:
    parent = list(range(len(signals)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    key_to_idx: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(signals):
        for k in _signal_org_keys(s) | _signal_person_keys(s):
            key_to_idx[k].append(i)

    for key, idxs in key_to_idx.items():
        if len(idxs) > MAX_SIGNALS_PER_CLUSTER_KEY:
            logger.warning("Skipping hub link key %r (%d signals)", key, len(idxs))
            continue
        for j in idxs[1:]:
            union(idxs[0], j)

    groups: dict[int, list[Signal]] = defaultdict(list)
    for i, s in enumerate(signals):
        groups[find(i)].append(s)

    ventures = [_build_venture(g) for g in groups.values()]
    ventures.sort(key=lambda v: v.score, reverse=True)
    return ventures
