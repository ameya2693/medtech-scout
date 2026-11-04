"""Data model for the person-centred tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional


@dataclass
class GrantRecord:
    """One domain-matching GtR project, with person *roles* preserved
    (the company-centric Signal flattens PI / Co-I / PM / student together)."""

    project_id: str
    title: str
    funder: str
    category: str
    track: str                      # "commercial" | "academic" | "neutral"
    start: Optional[date]
    end: Optional[date]
    url: str
    lead_org_id: str
    n_orgs: int
    people: list[tuple[str, str]]   # (GtR person UUID, "PI" | "Co-I")
    matched_terms: list[str]


@dataclass
class PersonGrant:
    """One person's involvement in one grant."""

    grant: GrantRecord
    role: str                       # "PI" | "Co-I"
    lead_org_name: str = ""
    lead_is_company: bool = False


@dataclass
class Person:
    """Keyed on the GtR person UUID. The same UUID on two projects is the same
    person, so no name matching is needed at this step (name-based joining
    only arrives with patents, step 3)."""

    person_id: str
    name: str = ""
    grants: list[PersonGrant] = field(default_factory=list)
    institutions: list[str] = field(default_factory=list)   # from PI-role grants only
    company_linked: bool = False    # PI on a grant whose lead org is a company
    in_region: bool = False         # institution matches the focus-region name list
    score: float = 0.0
    score_reasons: list[str] = field(default_factory=list)

    @property
    def pi_grants(self) -> list[PersonGrant]:
        return [g for g in self.grants if g.role == "PI"]

    @property
    def last_start(self) -> Optional[date]:
        dates = [g.grant.start for g in self.grants if g.grant.start]
        return max(dates) if dates else None

    @property
    def first_start(self) -> Optional[date]:
        dates = [g.grant.start for g in self.grants if g.grant.start]
        return min(dates) if dates else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "name": self.name,
            "institutions": self.institutions,
            "company_linked": self.company_linked,
            "in_region": self.in_region,
            "score": self.score,
            "n_grants": len(self.grants),
        }
