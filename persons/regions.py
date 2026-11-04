"""Approximate focus-region test, by institution NAME only.

GtR organisation payloads are not used for location here (that would need a
verified field), so each region is a name list: good enough to focus a report,
not authoritative. Extend the lists as needed. Regions combine with commas:
    --region london-cambridge,edinburgh
"""

from __future__ import annotations

import re

REGION_NAMES: dict[str, list[str]] = {
    "london-cambridge": [
        # London
        "imperial college", "university college london", r"\bucl\b", "king's college london",
        "kings college london", "queen mary", "london school of", "st george's",
        "royal veterinary college", "royal college of art", "institute of cancer research",
        "francis crick", "university of westminster", "university of greenwich",
        "city, university of london", "city university of london", "university of east london",
        "middlesex university", "brunel university", "london south bank", "kingston university",
        "london metropolitan", "university of the arts london", "university of london",
        # Cambridge
        "university of cambridge", "cambridge university", "anglia ruskin",
        "babraham", "sanger institute", "laboratory of molecular biology",
    ],
    "edinburgh": [
        # Specific names, not bare "edinburgh": that would also match companies
        # such as "Edinburgh Instruments Ltd".
        "university of edinburgh", "heriot-watt", "heriot watt", "edinburgh napier",
        "queen margaret university", "roslin institute", "nhs lothian",
        "royal infirmary of edinburgh",
    ],
}
_RE = {name: re.compile("|".join(patterns), re.I) for name, patterns in REGION_NAMES.items()}


def parse_regions(spec: str) -> list[str]:
    """'all' / '' -> [] (no filter); 'a,b' -> ['a', 'b']. Raises ValueError on unknown names."""
    names = [s.strip().lower() for s in (spec or "").split(",") if s.strip()]
    if not names or names == ["all"]:
        return []
    unknown = [n for n in names if n not in REGION_NAMES]
    if unknown:
        raise ValueError(f"Unknown region(s) {unknown}; choose from 'all' or {sorted(REGION_NAMES)} (comma-separated)")
    return list(dict.fromkeys(names))


def in_regions(institution: str, regions: list[str] | None = None) -> bool:
    """True if the institution name matches any of `regions` (all known regions when empty/None)."""
    if not institution:
        return False
    return any(_RE[r].search(institution) for r in (regions or list(REGION_NAMES)))


def in_london_cambridge(institution: str) -> bool:
    return in_regions(institution, ["london-cambridge"])
