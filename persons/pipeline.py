"""Orchestration: grants -> people -> filter -> score -> names. Network access
is injected (fetch_org / fetch_name) so the whole flow runs offline in tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Optional

from persons.build import build_people, resolve_lead_orgs, resolve_names
from persons.models import GrantRecord, Person
from persons.regions import parse_regions
from persons.score import score_people
from sources import gtr

COMPANY_LINKED_SHOWN = 15


@dataclass
class Result:
    ranked: list[Person]
    company_linked: list[Person]
    total_people: int
    new_orgs: dict[str, Any]
    new_names: dict[str, str]


def run(
    grants: list[GrantRecord],
    cache: dict[str, dict[str, Any]],
    *,
    region: str = "all",
    limit: int = 60,
    today: Optional[date] = None,
    fetch_org: Callable[[str], Optional[dict[str, Any]]] = gtr._fetch_org_details,
    fetch_name: Callable[[str, str], Optional[str]] = gtr._fetch_entity_name,
) -> Result:
    orgs, new_orgs = resolve_lead_orgs(grants, cache, fetch_org=fetch_org)
    regions = parse_regions(region)
    people = build_people(grants, orgs, regions)
    total = len(people)

    pool = list(people.values())
    if regions:
        # Institution is only known for PI roles, so Co-I-only people drop out.
        pool = [p for p in pool if p.in_region]

    scored = score_people(pool, today=today)
    ranked = [p for p in scored if not p.company_linked][:limit]
    company_linked = [p for p in scored if p.company_linked][:COMPANY_LINKED_SHOWN]

    new_names = resolve_names(ranked + company_linked, cache, fetch_name=fetch_name)
    return Result(ranked, company_linked, total, new_orgs, new_names)
