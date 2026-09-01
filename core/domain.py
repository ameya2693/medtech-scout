"""Domain definition for diagnostics / IVD / microfluidics.

This is the single place that encodes *what we're hunting for*. Tightening or
widening the net is a one-file edit. Keep it specific: precision beats recall
for a scouting tool, because a noisy report is one nobody reads.
"""

from __future__ import annotations

import re

# Keyword sets used for full-text matching against grant/patent abstracts.
# Grouped so we can require a "platform" term AND an "application/biology" term
# to fire, which cuts a lot of false positives (e.g. a pure-software ML grant
# that merely mentions "diagnosis" in passing).

PLATFORM_TERMS = [
    "microfluidic", "lab-on-chip", "lab on a chip", "lab-on-a-chip",
    "lateral flow", "point-of-care", "point of care", "poct",
    "biosensor", "immunoassay", "lateral-flow", "organ-on-chip",
    "paper-based assay", "digital pcr", "isothermal amplification",
    "loop-mediated", "lamp assay", "microarray", "electrochemical sensor",
    "surface plasmon resonance", "nanopore", "droplet microfluidic",
]

APPLICATION_TERMS = [
    "diagnostic", "diagnosis", "in vitro diagnostic", "ivd",
    "molecular diagnostic", "infectious disease", "biomarker",
    "liquid biopsy", "early detection", "screening test", "assay",
    "pathogen detection", "companion diagnostic", "self-test",
    "rapid test", "antigen test", "antibody test", "sepsis",
    "antimicrobial resistance", "amr",
]

# Strong single terms that fire on their own (highly specific to the niche).
STRONG_TERMS = [
    "microfluidic diagnostic", "lab-on-chip diagnostic",
    "point-of-care diagnostic", "in vitro diagnostic device",
    "digital pcr", "lateral flow assay",
]

# Cooperative Patent Classification prefixes most relevant to the niche.
# Used in Phase 2 (EPO) to pre-filter patents before text matching.
CPC_CLASSES = [
    "G01N33",   # investigating/analysing materials: bio testing, immunoassay
    "G01N27",   # electrochemical analysis (biosensors)
    "B01L3",    # microfluidic devices / handling small volumes
    "C12Q1",    # measuring/testing involving nucleic acids / enzymes
    "A61B5",    # diagnosis / measuring for diagnostic purposes
    "B01L7",    # thermal cyclers / temperature control for reactions
    "G01N21",   # optical investigation (fluorescence, SPR)
]

# UKRI lead funders most likely to fund translational diagnostics work.
# Kept as a MINOR tie-breaker in scoring now, not a dominant factor -- see
# core/score.py. Funder name alone is a weak proxy for "went to a company",
# since e.g. MRC/BBSRC/EPSRC mostly fund universities directly, while
# grantCategory and organisation-type (see below) are much more direct
# signals of the same thing.
PRIORITY_FUNDERS = {
    "MRC",            # Medical Research Council (incl. DPFS translational)
    "EPSRC",          # engineering (microfluidics, devices)
    "Innovate UK",
    "BBSRC",
    "NIHR",
}

_STRONG_RE = re.compile("|".join(re.escape(t) for t in STRONG_TERMS), re.I)
_PLATFORM_RE = re.compile("|".join(re.escape(t) for t in PLATFORM_TERMS), re.I)
_APP_RE = re.compile("|".join(re.escape(t) for t in APPLICATION_TERMS), re.I)


def matches_domain(text: str) -> tuple[bool, list[str]]:
    """Return (is_match, matched_terms).

    Match rule: a strong term alone, OR (a platform term AND an application
    term). This keeps precision high.
    """
    if not text:
        return False, []
    matched: list[str] = []

    strong = _STRONG_RE.findall(text)
    if strong:
        matched.extend(sorted({m.lower() for m in strong}))
        return True, matched

    plat = _PLATFORM_RE.findall(text)
    app = _APP_RE.findall(text)
    if plat and app:
        matched.extend(sorted({m.lower() for m in plat}))
        matched.extend(sorted({m.lower() for m in app}))
        return True, matched

    return False, []


def gtr_search_terms() -> list[str]:
    """Query strings to send to the GtR API. Broad-ish on the wire, then we
    tighten with matches_domain() locally (the API's full-text search is fuzzy)."""
    return [
        "microfluidic diagnostic",
        "point-of-care diagnostic",
        "lab-on-chip",
        "in vitro diagnostic",
        "biosensor diagnostic",
        "lateral flow assay",
    ]


# --- Company vs. academic-institution detection -----------------------------
# Used to score "which companies are getting funded" higher than "which
# universities are getting funded" -- a direct ask, not a proxy. Matching is
# deliberately simple and name-based: a legal-entity suffix is a strong,
# language-independent signal across UK and EU organisation names alike.

COMPANY_SUFFIXES = [
    "ltd", "limited", "llc", "plc", "inc", "incorporated", "corp", "corporation",
    "gmbh", "sa", "s.a.", "srl", "s.r.l.", "bv", "b.v.", "ag", "ab", "oy",
    "nv", "n.v.", "sarl", "s.a.r.l.", "kft", "sp. z o.o.", "spzoo",
]
ACADEMIC_MARKERS = [
    "university", "universit", "college", "institute of", "school of",
    "hospital", "nhs", "polytechnic", "academy of", "centre national",
    "national oceanography centre", "fraunhofer", "cnrs", "max planck",
    "national physical laboratory", "npl management",
]

_COMPANY_SUFFIX_RE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in COMPANY_SUFFIXES) + r")\b\.?", re.I
)
_ACADEMIC_MARKER_RE = re.compile(
    "|".join(re.escape(m) for m in ACADEMIC_MARKERS), re.I
)


def is_company_org(name: str) -> bool:
    """True if an organisation name looks like an actual company rather than
    a university/HEI/public research body. Suffix match wins if present
    (fairly unambiguous); otherwise fall back to "doesn't look academic"."""
    if not name:
        return False
    if _COMPANY_SUFFIX_RE.search(name):
        return True
    if _ACADEMIC_MARKER_RE.search(name):
        return False
    # Ambiguous (no suffix, no academic marker) -- default to "not a company"
    # since precision matters more than recall here; false negatives (missing
    # a company) are cheaper than false positives (crediting a research
    # institute as if it were a startup).
    return False


def any_company_org(organisations: list[str]) -> bool:
    return any(is_company_org(o) for o in organisations)


# --- Commercial vs. academic-track grant classification ---------------------
# GtR's `grantCategory` field distinguishes translational/commercial funding
# (money that goes toward building a product/company) from academic-track
# funding (money that trains a person). We want to surface the former.

COMMERCIAL_GRANT_CATEGORY_TERMS = [
    "grant for r&d", "collaborative r&d", "feasibility study",
    "knowledge transfer partnership", "ktp", "smart grant", "innovate uk",
]
ACADEMIC_GRANT_CATEGORY_TERMS = [
    "studentship", "fellowship", "training grant", "doctoral", "phd",
    "career development", "early career",
]

_COMMERCIAL_CATEGORY_RE = re.compile(
    "|".join(re.escape(t) for t in COMMERCIAL_GRANT_CATEGORY_TERMS), re.I
)
_ACADEMIC_CATEGORY_RE = re.compile(
    "|".join(re.escape(t) for t in ACADEMIC_GRANT_CATEGORY_TERMS), re.I
)


def grant_category_track(category: str) -> str:
    """Classify a GtR grantCategory string as 'commercial', 'academic', or
    'neutral' (e.g. plain 'Research Grant', which doesn't clearly indicate
    either track). Used by core.score to weight spin-out-track funding above
    pure PhD/fellowship funding, per the project's explicit priority."""
    if not category:
        return "neutral"
    if _COMMERCIAL_CATEGORY_RE.search(category):
        return "commercial"
    if _ACADEMIC_CATEGORY_RE.search(category):
        return "academic"
    return "neutral"
