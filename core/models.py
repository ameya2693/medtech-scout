"""Core data models for the venture-scouting pipeline.

A `Signal` is one observation from one source (a grant, a patent, an
incorporation). Signals are the atomic unit everything else is built from:
they get scored individually and, from Phase 4, clustered into `Venture`
candidates that aggregate signals about the same underlying group/company.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from enum import Enum
from typing import Any, Optional


class SignalType(str, Enum):
    """Where in the venture funnel a signal sits (earliest -> latest)."""

    GRANT = "grant"            # translational/commercialisation funding
    PATENT = "patent"          # IP being protected (pre-spinout tell)
    INCORPORATION = "incorporation"  # company actually formed
    FUNDING_ROUND = "funding_round"  # equity raised


@dataclass
class Signal:
    """One normalized observation from one source.

    Every source client converts its raw payload into a list of these, so the
    rest of the pipeline never has to know about source-specific schemas.
    """

    source: str                      # e.g. "gtr", "epo", "companies_house"
    signal_type: SignalType
    title: str
    summary: str = ""                # abstract / description, trimmed
    organisations: list[str] = field(default_factory=list)  # institutions/companies
    people: list[str] = field(default_factory=list)         # PIs / inventors / officers
    signal_date: Optional[date] = None
    url: str = ""
    external_id: str = ""            # source-native id, used for dedup
    raw: dict[str, Any] = field(default_factory=dict)  # original record, kept for debugging
    # populated by core.score
    score: float = 0.0
    score_reasons: list[str] = field(default_factory=list)

    def dedup_key(self) -> str:
        return f"{self.source}:{self.external_id or self.title}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["signal_type"] = self.signal_type.value
        d["signal_date"] = self.signal_date.isoformat() if self.signal_date else None
        d.pop("raw", None)  # don't serialise the bulky raw payload into reports/state
        return d


@dataclass
class Venture:
    """A candidate venture: a cluster of signals about the same group/company.

    Built in Phase 4. Defined here so the report layer can already render it.
    """

    name: str                        # best-guess institution or company name
    signals: list[Signal] = field(default_factory=list)
    score: float = 0.0
    score_reasons: list[str] = field(default_factory=list)

    @property
    def signal_types(self) -> set[SignalType]:
        return {s.signal_type for s in self.signals}

    @property
    def institutions(self) -> set[str]:
        out: set[str] = set()
        for s in self.signals:
            out.update(s.organisations)
        return out
