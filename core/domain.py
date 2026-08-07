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
# Used to up-weight (not exclude) grant signals in scoring.
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
