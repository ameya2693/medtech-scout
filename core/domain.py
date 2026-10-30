"""Domain definitions (search profiles) for the venture scout.

Select a profile with the SCOUT_DOMAIN environment variable:
    SCOUT_DOMAIN=medtech   (default)  diagnostics / IVD / microfluidics (+ organ-on-chip, therapeutics-adjacent)
    SCOUT_DOMAIN=climate              climate tech: storage, hydrogen, carbon removal, heat, grid, solar

Everything else (company detection, grant-track classification, scoring,
clustering) is domain-independent.
"""

from __future__ import annotations

import os
import re

PROFILES: dict[str, dict] = {
    "medtech": {
        "label": "diagnostics / IVD / microfluidics",
        "platform_terms": [
            "microfluidic", "lab-on-chip", "lab on a chip", "lab-on-a-chip",
            "lateral flow", "point-of-care", "point of care", "poct",
            "biosensor", "immunoassay", "lateral-flow", "organ-on-chip",
            "organ-on-a-chip", "microphysiological", "paper-based assay",
            "digital pcr", "isothermal amplification", "loop-mediated",
            "lamp assay", "microarray", "electrochemical sensor",
            "surface plasmon resonance", "nanopore", "droplet microfluidic",
            "crispr", "wearable biosensor", "cell-free",
        ],
        "application_terms": [
            "diagnostic", "diagnosis", "in vitro diagnostic", "ivd",
            "molecular diagnostic", "infectious disease", "biomarker",
            "liquid biopsy", "early detection", "screening test", "assay",
            "pathogen detection", "companion diagnostic", "self-test",
            "rapid test", "antigen test", "antibody test", "sepsis",
            "antimicrobial resistance", "amr",
            "therapeutic", "drug discovery", "drug screening", "drug delivery",
        ],
        "strong_terms": [
            "microfluidic diagnostic", "lab-on-chip diagnostic",
            "point-of-care diagnostic", "in vitro diagnostic device",
            "digital pcr", "lateral flow assay", "organ-on-chip",
            "organ-on-a-chip", "microphysiological system",
        ],
        "gtr_search_terms": [
            "microfluidic diagnostic", "point-of-care diagnostic", "lab-on-chip",
            "in vitro diagnostic", "biosensor diagnostic", "lateral flow assay",
            # expansion
            "lab-on-a-chip", "organ-on-chip", "microphysiological system",
            "CRISPR diagnostic", "isothermal amplification", "digital PCR",
            "wearable biosensor", "sepsis diagnostic",
            "antimicrobial resistance diagnostic", "drug delivery microfluidic",
        ],
        "priority_funders": {"MRC", "EPSRC", "Innovate UK", "BBSRC", "NIHR"},
    },
    "climate": {
        "label": "climate tech (storage, hydrogen, carbon removal, heat, grid, solar)",
        "platform_terms": [
            "electrolyser", "electrolyzer", "electrolysis", "fuel cell",
            "solid-state battery", "sodium-ion", "lithium-ion", "flow battery",
            "battery", "supercapacitor", "perovskite", "photovoltaic",
            "solar cell", "heat pump", "thermal storage", "geothermal",
            "direct air capture", "carbon capture", "biochar", "enhanced weathering",
            "wind turbine", "offshore wind", "tidal", "smart grid",
            "power electronics", "sustainable aviation fuel", "e-fuel",
            "green hydrogen", "ammonia", "waste heat", "membrane",
        ],
        "application_terms": [
            "decarbonisation", "decarbonization", "net zero", "net-zero",
            "emissions reduction", "greenhouse gas", "carbon removal",
            "energy storage", "renewable", "clean energy", "low carbon",
            "low-carbon", "grid", "electrification", "circular economy",
            "recycling", "sustainable", "climate",
        ],
        "strong_terms": [
            "direct air capture", "green hydrogen", "solid-state battery",
            "long duration energy storage", "long-duration energy storage",
            "carbon dioxide removal", "sustainable aviation fuel",
            "grid-scale storage", "heat pump", "enhanced weathering",
        ],
        "gtr_search_terms": [
            "direct air capture", "green hydrogen electrolyser",
            "solid-state battery", "sodium-ion battery",
            "long duration energy storage", "carbon dioxide removal",
            "heat pump decarbonisation", "perovskite solar",
            "sustainable aviation fuel", "grid-scale storage",
            "battery recycling", "geothermal",
        ],
        "priority_funders": {"EPSRC", "Innovate UK", "NERC", "BBSRC", "STFC"},
    },
}

DOMAIN_NAME = os.environ.get("SCOUT_DOMAIN", "medtech").strip().lower() or "medtech"
if DOMAIN_NAME not in PROFILES:
    raise SystemExit(
        f"Unknown SCOUT_DOMAIN {DOMAIN_NAME!r}; choose one of {sorted(PROFILES)}"
    )
_PROFILE = PROFILES[DOMAIN_NAME]

DOMAIN_LABEL = _PROFILE["label"]
PLATFORM_TERMS = _PROFILE["platform_terms"]
APPLICATION_TERMS = _PROFILE["application_terms"]
STRONG_TERMS = _PROFILE["strong_terms"]
PRIORITY_FUNDERS = _PROFILE["priority_funders"]

# Used by the (not yet built) patent source.
CPC_CLASSES = [
    "G01N33", "G01N27", "B01L3", "C12Q1", "A61B5", "B01L7", "G01N21",
] if DOMAIN_NAME == "medtech" else [
    "H01M", "C25B", "Y02E", "Y02P", "Y02C", "F24T", "B01D53",
]

_STRONG_RE = re.compile("|".join(re.escape(t) for t in STRONG_TERMS), re.I)
_PLATFORM_RE = re.compile("|".join(re.escape(t) for t in PLATFORM_TERMS), re.I)
_APP_RE = re.compile("|".join(re.escape(t) for t in APPLICATION_TERMS), re.I)


def matches_domain(text: str) -> tuple[bool, list[str]]:
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
    return list(_PROFILE["gtr_search_terms"])


# --- Company vs. academic-institution detection -----------------------------

COMPANY_SUFFIXES = [
    "ltd", "limited", "llc", "plc", "inc", "incorporated", "corp", "corporation",
    "gmbh", "sa", "s.a.", "srl", "s.r.l.", "bv", "b.v.", "ag", "ab", "oy",
    "nv", "n.v.", "sarl", "s.a.r.l.", "kft", "sp. z o.o.", "spzoo",
]
ACADEMIC_MARKERS = [
    "university", "universit", "college", "institute of", "institute",
    "institut ", "school of", "hospital", "ospedale", "klinikum", "nhs",
    "polytechnic", "academy of", "centre national", "national oceanography centre",
    "fraunhofer", "cnrs", "max planck", "helmholtz", "forschung", "research centre",
    "research center", "competence centre", "competence center", "csem",
    "national physical laboratory", "npl management", "foundation", "fundacion",
    "fundacao", "stichting", "society", "alliance", "ministry",
]

_COMPANY_SUFFIX_RE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in COMPANY_SUFFIXES) + r")\b\.?", re.I
)
_ACADEMIC_MARKER_RE = re.compile(
    "|".join(re.escape(m) for m in ACADEMIC_MARKERS), re.I
)


def is_company_org(name: str) -> bool:
    """True for commercial organisations. Academic / research-centre / hospital /
    non-profit markers win over a legal suffix, so 'Helmholtz Zentrum ... GmbH'
    or 'Polymer Competence Center Leoben GmbH' are NOT companies."""
    if not name:
        return False
    if _ACADEMIC_MARKER_RE.search(name):
        return False
    return bool(_COMPANY_SUFFIX_RE.search(name))


def any_company_org(organisations: list[str]) -> bool:
    return any(is_company_org(o) for o in organisations)


# --- Commercial vs. academic-track grant classification ---------------------

COMMERCIAL_GRANT_CATEGORY_TERMS = [
    "grant for r&d", "collaborative r&d", "feasibility study",
    "knowledge transfer partnership", "ktp", "smart grant", "innovate uk",
    "fast start",
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
    if not category:
        return "neutral"
    if _COMMERCIAL_CATEGORY_RE.search(category):
        return "commercial"
    if _ACADEMIC_CATEGORY_RE.search(category):
        return "academic"
    return "neutral"
