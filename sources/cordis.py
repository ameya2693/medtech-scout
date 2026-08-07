"""Client for CORDIS -- the EU's open dataset of Horizon Europe / H2020
projects. This is the EU-wide analog to UKRI Gateway to Research: covers
every EU member state plus associated countries (Switzerland re-associated
1 Jan 2025), including France and Switzerland specifically.

Access model is different from GtR by necessity: CORDIS's only stateless,
no-login, no-API-key route is the bulk open-data download -- a zip archive
published monthly containing several linked CSV files (projects,
organisations, EuroSciVoc classification, etc). There is no simple paginated
"search by keyword" REST endpoint the way GtR has one; the closer equivalent
(the Data Extraction Tool API) requires a CORDIS account and a UI-configured
extraction task first, which is too much friction for this pipeline.

IMPORTANT -- verify before relying on this: the exact zip filename/URL and
internal CSV file names below are inferred from CORDIS's own documentation
and multiple independent third-party parsing scripts (all agreeing on the
shape), but were NOT live-tested from this environment (this sandbox's
network doesn't reach cordis.europa.eu). If HORIZON_ZIP_CANDIDATES all fail,
or the CSV column names don't match what EXPECTED_PROJECT_COLUMNS /
EXPECTED_ORG_COLUMNS look for, this module logs the real zip contents and
real column headers it found rather than silently guessing -- please paste
that output back so the parsing can be corrected against ground truth,
exactly like the GtR client was.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import date, datetime
from typing import Any, Optional

import requests

from core.domain import matches_domain
from core.models import Signal, SignalType

logger = logging.getLogger(__name__)

# Candidate URLs for the monthly Horizon Europe bulk export, tried in order.
# The H2020 predecessor's URL (confirmed working, cited by multiple
# independent third-party scripts) is "cordis-h2020projects-csv.zip" --
# these Horizon Europe candidates follow that same naming convention but
# have not been confirmed against a live response.
HORIZON_ZIP_CANDIDATES = [
    "https://cordis.europa.eu/data/cordis-HORIZONprojects-csv.zip",
    "https://cordis.europa.eu/data/cordis-horizonprojects-csv.zip",
    "https://cordis.europa.eu/data/cordis-HorizonEuropeprojects-csv.zip",
]
# Human page to fall back to if all candidate URLs fail -- includes a
# "Download all Horizon Europe projects" button with the real, current link.
DATASET_LANDING_PAGE = (
    "https://data.europa.eu/data/datasets/"
    "cordis-eu-research-projects-under-horizon-europe-2021-2027"
)

REQUEST_TIMEOUT = 120  # bulk file, can be tens of MB

# Countries of particular interest for this pipeline (France, Switzerland).
# Not a hard filter -- domain match is still the primary gate -- just a
# scoring boost, same spirit as GtR's priority-funder boost.
TARGET_COUNTRIES = {"FR", "CH"}

# Column-name candidates to search for (case-insensitive substring match)
# within each CSV's header row. Several plausible variants are listed
# because the exact CORDIS export column names were not confirmed live.
PROJECT_COLUMN_CANDIDATES = {
    "id": ["id", "projectid", "rcn"],
    "acronym": ["acronym"],
    "title": ["title"],
    "objective": ["objective", "abstract"],
    "start_date": ["startdate", "start_date"],
    "end_date": ["enddate", "end_date"],
}
ORG_COLUMN_CANDIDATES = {
    "project_id": ["projectid", "project_id", "id"],
    "name": ["name", "organisationname", "organizationname"],
    "country": ["country"],
    "role": ["role"],
}


class CordisClientError(RuntimeError):
    pass


def _find_column(header: list[str], candidates: list[str]) -> Optional[str]:
    """Case-insensitive substring match of candidates against actual header
    names. Returns the actual header string to use as the dict key, or None."""
    lower_map = {h.lower(): h for h in header}
    for cand in candidates:
        for lower_h, actual_h in lower_map.items():
            if cand == lower_h or cand in lower_h:
                return actual_h
    return None


def _resolve_columns(header: list[str], candidate_map: dict[str, list[str]], file_label: str) -> dict[str, str]:
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for field, candidates in candidate_map.items():
        col = _find_column(header, candidates)
        if col is None:
            missing.append(field)
        else:
            resolved[field] = col
    if missing:
        raise CordisClientError(
            f"CORDIS {file_label}: could not find columns for {missing} in actual header: {header}. "
            "The CSV shape doesn't match what this client expects -- please paste this header "
            "back so the column mapping can be fixed against the real export."
        )
    return resolved


def _parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _download_zip(url: str) -> Optional[zipfile.ZipFile]:
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("CORDIS: request to %s failed: %s", url, exc)
        return None
    if resp.status_code != 200:
        logger.warning("CORDIS: %s returned HTTP %s", url, resp.status_code)
        return None
    try:
        return zipfile.ZipFile(io.BytesIO(resp.content))
    except zipfile.BadZipFile:
        logger.warning("CORDIS: %s did not return a valid zip file (got %d bytes)", url, len(resp.content))
        return None


def _find_member(zf: zipfile.ZipFile, keyword: str) -> Optional[str]:
    for name in zf.namelist():
        if keyword.lower() in name.lower() and name.lower().endswith(".csv"):
            return name
    return None


def _read_csv_member(zf: zipfile.ZipFile, member_name: str) -> tuple[list[str], list[dict[str, str]]]:
    with zf.open(member_name) as f:
        text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
        reader = csv.reader(text, delimiter=";")  # CORDIS exports are typically semicolon-delimited
        rows = list(reader)
    if not rows:
        raise CordisClientError(f"CORDIS: {member_name} is empty")
    header = rows[0]
    # Fall back to comma delimiter if semicolon parsing produced a single-column header
    # (a defensive check, since the delimiter convention wasn't confirmed live).
    if len(header) <= 1:
        with zf.open(member_name) as f:
            text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
            reader = csv.reader(text, delimiter=",")
            rows = list(reader)
        header = rows[0]
    dict_rows = [dict(zip(header, row)) for row in rows[1:]]
    return header, dict_rows


def fetch_signals() -> list[Signal]:
    """Download the Horizon Europe bulk export, parse projects + organisations,
    filter to the domain, and return Signals. Returns an empty list (with a
    clear logged reason) rather than raising, if the download or parsing
    fails -- CORDIS being unavailable shouldn't take down a run that also
    includes GtR signals.
    """
    zf: Optional[zipfile.ZipFile] = None
    for url in HORIZON_ZIP_CANDIDATES:
        logger.info("CORDIS: trying %s", url)
        zf = _download_zip(url)
        if zf is not None:
            logger.info("CORDIS: successfully downloaded from %s", url)
            break

    if zf is None:
        logger.error(
            "CORDIS: could not download the Horizon Europe bulk export from any candidate URL. "
            "Please visit %s, click 'Download all Horizon Europe projects', and either point "
            "this client at the real URL or extract the zip locally and adapt fetch_signals() "
            "to read from disk.",
            DATASET_LANDING_PAGE,
        )
        return []

    project_member = _find_member(zf, "project")
    org_member = _find_member(zf, "organization") or _find_member(zf, "organisation")

    if project_member is None or org_member is None:
        logger.error(
            "CORDIS: expected a 'project' and an 'organization'/'organisation' CSV inside the "
            "zip but found these members instead: %s. Please paste this list back so the file "
            "lookup can be corrected.",
            zf.namelist(),
        )
        return []

    try:
        proj_header, proj_rows = _read_csv_member(zf, project_member)
        proj_cols = _resolve_columns(proj_header, PROJECT_COLUMN_CANDIDATES, project_member)

        org_header, org_rows = _read_csv_member(zf, org_member)
        org_cols = _resolve_columns(org_header, ORG_COLUMN_CANDIDATES, org_member)
    except CordisClientError as exc:
        logger.error(str(exc))
        return []

    # Build project_id -> [(name, country), ...]
    orgs_by_project: dict[str, list[tuple[str, str]]] = {}
    for row in org_rows:
        pid = row.get(org_cols["project_id"], "")
        name = row.get(org_cols["name"], "")
        country = (row.get(org_cols["country"], "") or "").upper()
        if pid and name:
            orgs_by_project.setdefault(pid, []).append((name, country))

    signals: list[Signal] = []
    for row in proj_rows:
        pid = row.get(proj_cols["id"], "")
        title = (row.get(proj_cols["title"]) or "").strip()
        objective = (row.get(proj_cols["objective"]) or "").strip()
        combined = f"{title} {objective}"

        is_match, matched_terms = matches_domain(combined)
        if not is_match:
            continue

        org_pairs = orgs_by_project.get(pid, [])
        org_countries = {c for _, c in org_pairs if c}

        sig = Signal(
            source="cordis",
            signal_type=SignalType.GRANT,
            title=title or row.get(proj_cols.get("acronym", ""), "") or "(untitled CORDIS project)",
            summary=objective[:600],
            organisations=[f"{name} ({country})" if country else name for name, country in org_pairs],
            people=[],  # Horizon Europe bulk export doesn't include PI names outside ERC grants
            signal_date=_parse_date(row.get(proj_cols.get("start_date", ""), "")),
            url=f"https://cordis.europa.eu/project/id/{pid}" if pid else "",
            external_id=pid,
            raw={"org_countries": sorted(org_countries)},
        )
        sig.score_reasons.append(f"domain match: {', '.join(matched_terms[:4])}")
        if org_countries & TARGET_COUNTRIES:
            sig.score_reasons.append(
                f"target country present: {', '.join(sorted(org_countries & TARGET_COUNTRIES))}"
            )
        signals.append(sig)

    logger.info("CORDIS: %d domain-matching signals found", len(signals))
    return signals
