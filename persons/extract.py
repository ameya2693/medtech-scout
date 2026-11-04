"""Role-preserving extraction of grants from GtR project payloads.

Only fields already confirmed against live GtR payloads in sources/gtr.py are
parsed here: `links.link[]` (`rel`, `href`, FUND `start`/`end` in epoch ms),
`leadFunder`, `grantCategory`, `identifiers.identifier[]`, `title`,
`abstractText` / `techAbstractText`.

What differs from sources.gtr._project_to_signal: person links are kept with
their relation type. Only PI_PER and COI_PER are used. PM_PER (project
managers) and STUDENT_PER (students) are not founder candidates at this
stage and are ignored.
"""

from __future__ import annotations

import html
import logging
from typing import Any, Iterable, Optional

from core.domain import grant_category_track, gtr_search_terms, matches_domain
from persons.models import GrantRecord
from sources import gtr

logger = logging.getLogger(__name__)

ROLE_BY_REL = {"PI_PER": "PI", "COI_PER": "Co-I"}


def extract_roles(project: dict[str, Any]) -> list[tuple[str, str]]:
    """(person UUID, role) pairs. A person linked as both PI and Co-I on the
    same project is recorded once, as PI."""
    roles: dict[str, str] = {}
    for link in gtr._iter_links(project):
        rel = (link.get("rel") or "").upper()
        href = link.get("href", "")
        role = ROLE_BY_REL.get(rel)
        if not role or not href:
            continue
        pid = gtr._uuid_from_href(href)
        if roles.get(pid) != "PI":
            roles[pid] = role
    return list(roles.items())


def extract_grant(project: dict[str, Any]) -> Optional[GrantRecord]:
    """Convert a raw GtR project into a GrantRecord, or None if it is out of
    domain or has no PI / Co-I links."""
    try:
        title = html.unescape(project.get("title", "")).strip()
        abstract = html.unescape(project.get("abstractText") or "").strip()
        technical = html.unescape(project.get("techAbstractText") or "").strip()
        is_match, terms = matches_domain(f"{title} {abstract or technical}")
        if not is_match:
            return None

        people = extract_roles(project)
        if not people:
            return None

        link_data = gtr._extract_link_data(project)
        rcuk_ref = gtr._extract_rcuk_ref(project)
        category = (project.get("grantCategory") or "").strip()
        return GrantRecord(
            project_id=project.get("id", ""),
            title=title,
            funder=(project.get("leadFunder") or "").strip(),
            category=category,
            track=grant_category_track(category),
            start=link_data["fund_start"],
            end=link_data["fund_end"],
            url=f"https://gtr.ukri.org/projects?ref={rcuk_ref}" if rcuk_ref else project.get("href", ""),
            lead_org_id=link_data["lead_org_id"],
            n_orgs=len(link_data["org_ids"]),
            people=people,
            matched_terms=terms,
        )
    except Exception as exc:  # noqa: BLE001 - one bad record must not kill the run
        logger.warning("Skipping malformed GtR project record: %s", exc)
        return None


def grants_from_projects(projects: Iterable[dict[str, Any]]) -> list[GrantRecord]:
    out: dict[str, GrantRecord] = {}
    for p in projects:
        g = extract_grant(p)
        if g is not None:
            out[g.project_id or g.title] = g   # identical across search terms
    return list(out.values())


def fetch_grants() -> list[GrantRecord]:
    """Run every domain search term against GtR (same terms as the scout)."""
    found: dict[str, GrantRecord] = {}
    for term in gtr_search_terms():
        logger.info("GtR: searching %r", term)
        try:
            for raw in gtr.search_projects(term):
                g = extract_grant(raw)
                if g is not None:
                    found[g.project_id or g.title] = g
        except gtr.GtRClientError as exc:
            logger.error("GtR search failed for term %r: %s", term, exc)
            continue
    logger.info("GtR: %d unique domain-matching grants with a PI / Co-I", len(found))
    return list(found.values())
