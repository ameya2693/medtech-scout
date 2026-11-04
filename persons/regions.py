"""Approximate focus-region test, by institution NAME only.

GtR organisation payloads are not used for location here (that would need a
verified field), so this is a name list: good enough to focus a report on
London + Cambridge, not authoritative. Extend as needed.
"""

from __future__ import annotations

import re

LONDON_CAMBRIDGE_NAMES = [
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
]
_RE = re.compile("|".join(LONDON_CAMBRIDGE_NAMES), re.I)


def in_london_cambridge(institution: str) -> bool:
    return bool(institution and _RE.search(institution))
