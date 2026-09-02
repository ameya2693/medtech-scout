"""Client for UKRI's Gateway to Research (GtR) API.

Free, no auth required. Covers all seven UK research councils plus Innovate
UK and Research England -- the earliest-signal source in the funnel (a grant
today can mean a spinout in 1-3 years).

API docs: https://gtr.ukri.org/resources/api.html
Base URL: https://gtr.ukri.org/gtr/api

Field shapes below were confirmed against a live response (not assumed from
documentation), specifically:
  - `leadFunder` is a flat top-level string (e.g. "EPSRC", "Innovate UK").
  - The usable date lives on the project's `FUND` link (not the project's
    own `start`/`end`, which are usually null), as epoch-millisecond ints.
  - The RCUK grant reference (used for the public gtr.ukri.org URL) is under
    `identifiers.identifier[]` where `type == "RCUK"`.
  - Organisations and people are NOT embedded in the project payload -- the
    `links.link[]` array only gives a `rel` (e.g. LEAD_ORG, PI_PER) and an
    `href` containing a UUID. Resolving a UUID to a human name requires a
    second GET to /gtr/api/organisations/{uuid} (name in `name`) or
    /gtr/api/persons/{uuid} (name in `firstName` + `surname`).
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any, Iterator, Optional

import requests

from core.domain import gtr_search_terms, matches_domain, PRIORITY_FUNDERS
from core.models import Signal, SignalType
from state.store import load_name_cache, save_name_cache

logger = logging.getLogger(__name__)

BASE_URL = "https://gtr.ukri.org/gtr/api"
PAGE_SIZE = 25          # GtR requires page size >= 10; 25 keeps request count reasonable
MAX_PAGES_PER_QUERY = 8  # safety cap: 8 * 25 = 200 records per search term (see README re: tuning)
REQUEST_TIMEOUT = 30
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2

# Relation types on a project's links that point at organisations vs people.
ORG_RELS = {"LEAD_ORG", "PARTICIPANT_ORG", "STUDENT_PP_ORG"}
PERSON_RELS = {"PI_PER", "COI_PER", "PM_PER", "STUDENT_PER"}

HEADERS = {
    "Accept": "application/vnd.rcuk.gtr.json-v7",
    "User-Agent": "medtech-scout/0.1 (research/portfolio tool)",
}


class GtRClientError(RuntimeError):
    """Raised when the GtR API returns something we can't parse or use."""


def _get_with_retries(url: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    last_exc: Optional[Exception] = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("GtR request error (attempt %d/%d): %s", attempt, RETRY_ATTEMPTS, exc)
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:
                raise GtRClientError(
                    f"GtR returned 200 but non-JSON body (first 200 chars): {resp.text[:200]!r}"
                ) from exc

        if resp.status_code in (429, 500, 502, 503, 504):
            logger.warning(
                "GtR returned %s (attempt %d/%d), backing off", resp.status_code, attempt, RETRY_ATTEMPTS
            )
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue

        raise GtRClientError(
            f"GtR request failed: HTTP {resp.status_code} for {resp.url}\n"
            f"Body (first 500 chars): {resp.text[:500]!r}"
        )

    raise GtRClientError(f"GtR request failed after {RETRY_ATTEMPTS} attempts: {last_exc}")


def _epoch_millis_to_date(value: Any) -> Optional[date]:
    """FUND link start/end come through as ints (epoch milliseconds). Accept
    int, numeric string, or None defensively -- real-world APIs are messy."""
    if value is None:
        return None
    try:
        millis = int(value)
        if millis <= 0:
            return None
        return datetime.utcfromtimestamp(millis / 1000).date()
    except (ValueError, OverflowError, OSError):
        return None


def _iter_links(project: dict[str, Any]) -> list[dict[str, Any]]:
    links = (project.get("links") or {}).get("link", [])
    if isinstance(links, dict):
        links = [links]
    return links or []


def _uuid_from_href(href: str) -> str:
    """hrefs look like http://gtr.ukri.org/gtr/api/organisations/<UUID> --
    the UUID is always the last path segment."""
    return href.rstrip("/").rsplit("/", 1)[-1]


def _extract_link_data(project: dict[str, Any]) -> dict[str, Any]:
    """Pull organisation UUIDs, person UUIDs, and the FUND date range out of
    a project's links in one pass."""
    org_ids: list[str] = []
    person_ids: list[str] = []
    fund_start: Optional[date] = None
    fund_end: Optional[date] = None

    for link in _iter_links(project):
        rel = (link.get("rel") or "").upper()
        href = link.get("href", "")
        if rel in ORG_RELS and href:
            org_ids.append(_uuid_from_href(href))
        elif rel in PERSON_RELS and href:
            person_ids.append(_uuid_from_href(href))
        elif rel == "FUND":
            fund_start = fund_start or _epoch_millis_to_date(link.get("start"))
            fund_end = fund_end or _epoch_millis_to_date(link.get("end"))

    return {
        "org_ids": list(dict.fromkeys(org_ids)),      # de-dupe, preserve order
        "person_ids": list(dict.fromkeys(person_ids)),
        "fund_start": fund_start,
        "fund_end": fund_end,
    }


def _extract_rcuk_ref(project: dict[str, Any]) -> str:
    identifiers = (project.get("identifiers") or {}).get("identifier", [])
    if isinstance(identifiers, dict):
        identifiers = [identifiers]
    for ident in identifiers or []:
        if (ident.get("type") or "").upper() == "RCUK":
            return ident.get("value", "")
    return ""


def _project_to_signal(project: dict[str, Any]) -> Optional[Signal]:
    """Convert one raw GtR project record into a Signal, or None if it
    doesn't match the domain filter. Org/person names are NOT resolved here
    (that would mean a network call per project, most of which get filtered
    out or never make the report) -- only UUIDs are stashed on `raw` for
    `resolve_names()` to fill in later, lazily, for the signals that survive
    scoring and the report limit."""
    try:
        title = project.get("title", "").strip()
        abstract = (project.get("abstractText") or "").strip()
        technical = (project.get("techAbstractText") or "").strip()
        summary = abstract or technical

        combined_text = f"{title} {summary}"
        is_match, matched_terms = matches_domain(combined_text)
        if not is_match:
            return None

        link_data = _extract_link_data(project)
        funder = (project.get("leadFunder") or "").strip()
        rcuk_ref = _extract_rcuk_ref(project)
        project_uuid = project.get("id", "")
        grant_category = (project.get("grantCategory") or "").strip()

        signal = Signal(
            source="gtr",
            signal_type=SignalType.GRANT,
            title=title,
            summary=summary[:600],
            organisations=[],   # filled in later by resolve_names()
            people=[],          # filled in later by resolve_names()
            signal_date=link_data["fund_start"],
            url=f"https://gtr.ukri.org/projects?ref={rcuk_ref}" if rcuk_ref else project.get("href", ""),
            external_id=project_uuid,
            raw={
                "org_ids": link_data["org_ids"],
                "person_ids": link_data["person_ids"],
                "grant_category": grant_category,
                "status": project.get("status", ""),
                "matched_terms": matched_terms,
            },
        )
        signal.score_reasons.append(f"domain match: {', '.join(matched_terms[:4])}")
        if funder:
            signal.score_reasons.append(f"funder: {funder}")
            if funder in PRIORITY_FUNDERS:
                signal.score_reasons.append("priority funder")
        if grant_category:
            signal.score_reasons.append(f"grant category: {grant_category}")
        return signal
    except Exception as exc:  # noqa: BLE001 - defensive, one bad record shouldn't kill the run
        logger.warning("Skipping malformed GtR project record: %s", exc)
        return None


def search_projects(query: str) -> Iterator[dict[str, Any]]:
    """Yield raw project dicts matching a free-text query, paginated."""
    page = 1
    while page <= MAX_PAGES_PER_QUERY:
        params = {"q": query, "p": page, "s": PAGE_SIZE}
        data = _get_with_retries(f"{BASE_URL}/projects", params)

        projects = data.get("project", [])
        if isinstance(projects, dict):
            projects = [projects]
        if not projects:
            break

        for p in projects:
            yield p

        total_pages = data.get("totalPages")
        if total_pages is not None and page >= int(total_pages):
            break
        if len(projects) < PAGE_SIZE:
            break
        page += 1


def fetch_signals() -> list[Signal]:
    """Run all domain search terms against GtR and return deduped Signals
    (organisations/people not yet resolved -- see resolve_names)."""
    signals: dict[str, Signal] = {}
    for term in gtr_search_terms():
        logger.info("GtR: searching %r", term)
        try:
            for raw_project in search_projects(term):
                sig = _project_to_signal(raw_project)
                if sig is None:
                    continue
                signals[sig.dedup_key()] = sig  # last write wins; records are identical across terms
        except GtRClientError as exc:
            logger.error("GtR search failed for term %r: %s", term, exc)
            continue  # keep going with other terms rather than aborting the whole run

    logger.info("GtR: %d unique domain-matching signals found", len(signals))
    return list(signals.values())


def _fetch_entity_name(entity_type: str, uuid: str) -> Optional[str]:
    """entity_type is 'persons' only now -- see _fetch_org_details for
    organisations, which need more than just a name."""
    try:
        data = _get_with_retries(f"{BASE_URL}/{entity_type}/{uuid}")
    except GtRClientError as exc:
        logger.warning("Could not resolve %s %s: %s", entity_type, uuid, exc)
        return None
    first = (data.get("firstName") or "").strip()
    last = (data.get("surname") or "").strip()
    full = " ".join(p for p in (first, last) if p)
    return full or None


def _fetch_org_details(uuid: str) -> Optional[dict[str, Any]]:
    """Fetch an organisation's name AND whether it carries a Companies House
    `regNumber` -- confirmed present in a live GtR organisation payload.
    Universities/HEIs don't have one; real companies do, which makes this a
    ground-truth signal for company-vs-university detection, more reliable
    than guessing from name suffixes (which fails on names with no Ltd/
    Limited/etc, e.g. "VETERINARY ISOTHERMAL MOLECULAR DIAGNOSTICS")."""
    try:
        data = _get_with_retries(f"{BASE_URL}/organisations/{uuid}")
    except GtRClientError as exc:
        logger.warning("Could not resolve organisations %s: %s", uuid, exc)
        return None
    name = (data.get("name") or "").strip()
    if not name:
        return None
    has_regnum = bool((data.get("regNumber") or "").strip())
    return {"name": name, "has_regnum": has_regnum}


def resolve_names(signals: list[Signal]) -> None:
    """Fill in `organisations`, `people`, and `raw["org_has_regnum"]` on each
    signal, in place.

    Call this AFTER scoring and limiting to the signals that will actually
    appear in the report -- resolving names is one extra HTTP call per
    not-yet-cached UUID, and most institutions/PIs repeat heavily across
    grants, so a persistent on-disk cache (state/gtr_name_cache.json) means
    this cost shrinks to near-zero after the first real run.

    Cache migration note: organisation cache entries used to be plain name
    strings (`{uuid: "Some Org"}`). They're now `{uuid: {"name": ...,
    "has_regnum": ...}}` so the regNumber signal can be cached too. Old
    string-valued entries are detected and re-fetched once (self-healing --
    no manual cache reset needed after deploying this).
    """
    cache = load_name_cache()
    org_cache: dict[str, Any] = cache.setdefault("organisations", {})
    person_cache: dict[str, str] = cache.setdefault("persons", {})

    all_org_ids = {oid for s in signals for oid in s.raw.get("org_ids", [])}
    all_person_ids = {pid for s in signals for pid in s.raw.get("person_ids", [])}

    def _needs_org_fetch(oid: str) -> bool:
        entry = org_cache.get(oid)
        if entry is None:
            return True
        if isinstance(entry, str):  # old cache format, missing has_regnum
            return True
        return False

    to_fetch_orgs = [oid for oid in all_org_ids if _needs_org_fetch(oid)]
    to_fetch_persons = [pid for pid in all_person_ids if pid not in person_cache]

    logger.info(
        "Resolving names: %d organisations (%d new/needing regNumber backfill), %d people (%d new)",
        len(all_org_ids), len(to_fetch_orgs), len(all_person_ids), len(to_fetch_persons),
    )

    for oid in to_fetch_orgs:
        details = _fetch_org_details(oid)
        if details:
            org_cache[oid] = details
    for pid in to_fetch_persons:
        name = _fetch_entity_name("persons", pid)
        if name:
            person_cache[pid] = name

    save_name_cache(cache)

    for s in signals:
        # Only touch signals that actually came from GtR's link-UUID model
        # (i.e. have an org_ids/person_ids key in raw at all). Other sources
        # -- CORDIS included -- populate `organisations`/`people` directly
        # themselves and must not be overwritten here just because they
        # don't have GtR-specific raw keys. This was a real bug: the first
        # merged GtR+CORDIS run clobbered every CORDIS signal's correctly
        # resolved organisation names back to empty, because this loop used
        # to run unconditionally over the whole merged signal list.
        if "org_ids" not in s.raw and "person_ids" not in s.raw:
            continue

        org_ids = s.raw.get("org_ids", [])
        org_names: list[str] = []
        has_regnum = False
        for oid in org_ids:
            entry = org_cache.get(oid)
            if entry is None:
                continue
            if isinstance(entry, str):  # shouldn't normally happen post-migration, but stay defensive
                org_names.append(entry)
                continue
            org_names.append(entry.get("name", ""))
            if entry.get("has_regnum"):
                has_regnum = True
        s.organisations = [n for n in org_names if n]
        s.raw["org_has_regnum"] = has_regnum
        s.people = [person_cache[pid] for pid in s.raw.get("person_ids", []) if pid in person_cache]
