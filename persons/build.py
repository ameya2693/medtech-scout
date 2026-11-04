"""Turn GrantRecords into Person records (keyed on GtR person UUID)."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Callable, Optional

from core.domain import _ACADEMIC_MARKER_RE, is_company_org
from persons.models import GrantRecord, Person, PersonGrant
from persons.regions import in_london_cambridge
from sources import gtr

logger = logging.getLogger(__name__)


def _org_needs_fetch(entry: Any) -> bool:
    # Mirrors sources.gtr.resolve_names: missing, legacy plain-string, or
    # cached before reg_number was stored -> fetch again.
    return entry is None or isinstance(entry, str) or "reg_number" not in entry


def resolve_lead_orgs(
    grants: list[GrantRecord],
    cache: dict[str, dict[str, Any]],
    fetch_org: Callable[[str], Optional[dict[str, Any]]] = gtr._fetch_org_details,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Resolve lead organisations only (a Co-I's own organisation is not
    identifiable from project links). Returns (entries by id, newly fetched)."""
    org_cache = cache.setdefault("organisations", {})
    ids = {g.lead_org_id for g in grants if g.lead_org_id}
    to_fetch = [i for i in ids if _org_needs_fetch(org_cache.get(i))]
    logger.info("Lead organisations: %d total, %d to fetch", len(ids), len(to_fetch))
    new: dict[str, Any] = {}
    for oid in to_fetch:
        details = fetch_org(oid)
        if details:
            org_cache[oid] = details
            new[oid] = details
    resolved: dict[str, dict[str, Any]] = {}
    for oid in ids:
        entry = org_cache.get(oid)
        if isinstance(entry, str):          # legacy entry that could not be refreshed
            entry = {"name": entry, "has_regnum": False, "reg_number": ""}
        if entry:
            resolved[oid] = entry
    return resolved, new


def lead_is_company(funder: str, entry: Optional[dict[str, Any]]) -> bool:
    """Same three signals the scout uses: a Companies House regNumber, a legal
    suffix, or an Innovate UK lead with no academic marker in its name."""
    if not entry:
        return False
    name = entry.get("name", "")
    if entry.get("has_regnum") or is_company_org(name):
        return True
    return funder == "Innovate UK" and bool(name) and not _ACADEMIC_MARKER_RE.search(name)


def build_people(grants: list[GrantRecord], orgs: dict[str, dict[str, Any]]) -> dict[str, Person]:
    people: dict[str, Person] = {}
    for g in grants:
        entry = orgs.get(g.lead_org_id)
        lead_name = entry.get("name", "") if entry else ""
        is_co = lead_is_company(g.funder, entry)
        for pid, role in g.people:
            p = people.setdefault(pid, Person(person_id=pid))
            p.grants.append(PersonGrant(grant=g, role=role, lead_org_name=lead_name, lead_is_company=is_co))

    for p in people.values():
        pi = p.pi_grants
        # Newest first, so the most-used (then most recent) institution leads.
        counts = Counter(pg.lead_org_name for pg in pi if pg.lead_org_name)
        p.institutions = [name for name, _ in counts.most_common()]
        p.company_linked = any(pg.lead_is_company for pg in pi)
        p.in_region = any(
            in_london_cambridge(pg.lead_org_name) for pg in pi if not pg.lead_is_company
        )
    logger.info("Built %d people from %d grants", len(people), len(grants))
    return people


def resolve_names(
    persons: list[Person],
    cache: dict[str, dict[str, Any]],
    fetch_name: Callable[[str, str], Optional[str]] = gtr._fetch_entity_name,
) -> dict[str, str]:
    """Fill Person.name from the cache, fetching only what is missing.
    Returns newly fetched names (to be saved to this tool's own cache)."""
    person_cache = cache.setdefault("persons", {})
    new: dict[str, str] = {}
    for p in persons:
        if p.person_id not in person_cache:
            name = fetch_name("persons", p.person_id)
            if name:
                person_cache[p.person_id] = name
                new[p.person_id] = name
        p.name = person_cache.get(p.person_id, "")
    logger.info("Names: %d resolved, %d newly fetched", sum(1 for p in persons if p.name), len(new))
    return new
