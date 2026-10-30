"""Company-anchored venture grouping.

A venture is a COMPANY; its signals are the distinct projects it takes part in.

Earlier versions used connected components (union-find): any shared organisation
merged two signals, and merges chained transitively. Large CORDIS consortia
(10-25 partners) therefore fused unrelated projects into one blob (19 signals
under "MOLOGIC LTD", earlier 114). Here nothing is merged transitively: each
signal is simply listed under every company that qualifies as an anchor.

Rules:
  * Only company organisations anchor (is_company_org; research centres,
    hospitals and non-profits are excluded) and never ORG_STOPLIST names.
  * Signals from big consortia (> MAX_ANCHOR_ORGS partners) do not anchor
    anybody: a company in a 20-partner network is not "that company's project".
  * The same project found via both GtR and CORDIS (Horizon Europe Guarantee)
    is de-duplicated by normalised title + start date before counting.
  * Company names are canonicalised (legal suffix and country tag stripped) so
    'RapidX Bio Ltd' / 'RAPIDX BIO LIMITED' / 'Rapidx Bio Ltd. (UK)' coincide.
  * A venture needs >= 2 distinct projects to be reported.
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
_LEGAL_SUFFIX_RE = re.compile(
    r"\s+(LTD|LIMITED|LLC|PLC|INC|INCORPORATED|CORP|CORPORATION|GMBH|SA|SRL|BV|AG|AB|OY|NV|SARL|PTY LTD|PTY)\.?$"
)
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9 ]+")

ORG_STOPLIST = {
    "NPL MANAGEMENT", "NATIONAL OCEANOGRAPHY CENTRE", "INSTITUT PASTEUR",
    # Large analytical-services / contract-research bodies that co-appear on
    # other companies' grants but are not the venture being scouted:
    "LGC", "LGC GROUP", "CENTRE FOR PROCESS INNOVATION",
    "TEKNOLOGIAN TUTKIMUSKESKUS VTT",
}
# Name fragments of generic consultancies / management shells that tell us
# nothing about a product company.
LOW_SIGNAL_NAME_PARTS = ("CONSULTING", "CONSULTANCY", " MANAGEMENT")

MAX_ANCHOR_ORGS = 8
MIN_PROJECTS_PER_VENTURE = 2
MAX_PROJECTS_PER_VENTURE = 12   # beyond this the "company" is almost certainly a hub
CLUSTER_SIZE_BONUS_PER_EXTRA_SIGNAL = 1.0
CLUSTER_SIZE_BONUS_CAP = 4.0
MULTI_SOURCE_BONUS = 0.5


def normalize_org_name(name: str) -> str:
    n = _COUNTRY_SUFFIX_RE.sub("", name or "")
    n = _TRAILING_PUNCT_RE.sub("", n.strip())
    return _WHITESPACE_RE.sub(" ", n).upper()


def company_key(name: str) -> str:
    """Canonical key: uppercase, no country tag, no legal suffix, no punctuation."""
    n = normalize_org_name(name)
    n = _LEGAL_SUFFIX_RE.sub("", n)
    n = _NON_ALNUM_RE.sub("", n)
    return _WHITESPACE_RE.sub(" ", n).strip()


def _project_signature(signal: Signal) -> str:
    title = _NON_ALNUM_RE.sub("", (signal.title or "").upper())
    title = _WHITESPACE_RE.sub(" ", title).strip()
    return f"{title}|{getattr(signal, 'signal_date', None)}"


def _anchor_companies(signal: Signal) -> dict[str, str]:
    """{company_key: display_name} for the companies this signal anchors."""
    orgs = signal.organisations or []
    if len(orgs) > MAX_ANCHOR_ORGS:
        return {}
    out: dict[str, str] = {}
    for org in orgs:
        display = normalize_org_name(org)
        if not display or not is_company_org(display):
            continue
        if display in ORG_STOPLIST or any(p in f" {display}" for p in LOW_SIGNAL_NAME_PARTS):
            continue
        key = company_key(org)
        if key and key not in ORG_STOPLIST:
            out.setdefault(key, display)
    return out


def _merge_duplicates(signals: list[Signal]) -> list[list[Signal]]:
    """Group cross-source duplicates of the same project."""
    groups: dict[str, list[Signal]] = defaultdict(list)
    for s in signals:
        groups[_project_signature(s)].append(s)
    return list(groups.values())


def build_ventures(signals: list[Signal]) -> list[Venture]:
    projects = _merge_duplicates(signals)

    by_company: dict[str, list[list[Signal]]] = defaultdict(list)
    names: dict[str, Counter] = defaultdict(Counter)
    for proj in projects:
        keys: dict[str, str] = {}
        for s in proj:
            keys.update(_anchor_companies(s))
        for key, display in keys.items():
            by_company[key].append(proj)
            names[key][display] += 1

    ventures: list[Venture] = []
    for key, projs in by_company.items():
        if len(projs) < MIN_PROJECTS_PER_VENTURE:
            continue
        if len(projs) > MAX_PROJECTS_PER_VENTURE:
            logger.warning("Skipping hub company %r (%d projects)", key, len(projs))
            continue
        flat = [s for proj in projs for s in proj]
        best = max(s.score for s in flat)
        extra = len(projs) - 1
        bonus = min(extra * CLUSTER_SIZE_BONUS_PER_EXTRA_SIGNAL, CLUSTER_SIZE_BONUS_CAP)
        sources = sorted({s.source for s in flat})
        reasons = [f"+{bonus:.1f} clustered: {len(projs)} distinct projects"]
        if len(sources) > 1:
            bonus += MULTI_SOURCE_BONUS
            reasons.append(f"+{MULTI_SOURCE_BONUS:.1f} multi-source ({', '.join(sources)})")
        dup = len(flat) - len(projs)
        if dup:
            reasons.append(f"{dup} cross-source duplicate(s) merged")
        ventures.append(
            Venture(
                name=names[key].most_common(1)[0][0],
                signals=sorted(flat, key=lambda s: s.score, reverse=True),
                score=best + bonus,
                score_reasons=reasons,
            )
        )
    ventures.sort(key=lambda v: v.score, reverse=True)
    return ventures
