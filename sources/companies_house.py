"""Companies House enrichment (Phase 3, enrichment half).

Looks up UK companies the scout already knows about -- by their official
registration number, which GtR supplies as `regNumber` -- and attaches
register facts to each signal: incorporation date and age, status, SIC codes,
who controls the company (PSC), and current directors.

Display-only for now: it does NOT change scores. Once we have seen a few
reports of real data we can decide how age / university-PSC should weigh in.

Free API: https://developer.company-information.service.gov.uk/
  * Needs an API key -> env var COMPANIES_HOUSE_API_KEY (optional; without it
    this module does nothing and the pipeline runs as before).
  * Auth: HTTP Basic, key as the username and an empty password.
  * Limit: 600 requests / 5 minutes (HTTP 429 beyond that). We throttle to
    well under it, cache every company for CACHE_TTL_DAYS, and stop cleanly
    at MAX_CALLS_PER_RUN.

Personal data: officers/PSC names are on the public register, but we keep
only name, role and appointment date -- no dates of birth or addresses.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import requests

from core.domain import _ACADEMIC_MARKER_RE
from core.models import Signal

logger = logging.getLogger(__name__)

BASE_URL = "https://api.company-information.service.gov.uk"
CACHE_PATH = Path(__file__).resolve().parent.parent / "state" / "companies_house_cache.json"
CACHE_TTL_DAYS = 30
MIN_SECONDS_BETWEEN_CALLS = 0.6      # ~500 calls / 5 min, under the 600 limit
MAX_CALLS_PER_RUN = 450
REQUEST_TIMEOUT = 20
MAX_OFFICERS_KEPT = 6
MAX_PSC_KEPT = 6


class CompaniesHouseError(RuntimeError):
    pass


def api_key() -> str:
    return (os.environ.get("COMPANIES_HOUSE_API_KEY") or "").strip()


def normalise_number(number: str) -> str:
    """Companies House numbers are 8 characters: digits zero-padded
    ('1234567' -> '01234567'), or a 2-letter prefix + digits ('SC123456')."""
    n = re.sub(r"\s+", "", (number or "")).upper()
    if n.isdigit():
        return n.zfill(8)
    return n


# ---------------------------------------------------------------- client

class Client:
    def __init__(self, key: str, max_calls: int = MAX_CALLS_PER_RUN):
        self.session = requests.Session()
        self.session.auth = (key, "")
        self.max_calls = max_calls
        self.calls = 0
        self._last_call = 0.0
        self.rate_limited = False

    @property
    def exhausted(self) -> bool:
        return self.rate_limited or self.calls >= self.max_calls

    def get(self, path: str) -> Optional[dict[str, Any]]:
        """GET a JSON resource. Returns None for 404 (unknown company / no
        data). Raises CompaniesHouseError for anything else we can't use."""
        if self.exhausted:
            raise CompaniesHouseError("call budget exhausted or rate limited")
        for attempt in range(1, 4):
            wait = MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            self.calls += 1
            try:
                resp = self.session.get(f"{BASE_URL}{path}", timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                logger.warning("Companies House request error (%d/3): %s", attempt, exc)
                time.sleep(2 * attempt)
                continue
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise CompaniesHouseError(f"non-JSON 200 for {path}") from exc
            if resp.status_code == 404:
                return None
            if resp.status_code == 401:
                self.rate_limited = True  # stop trying; key is wrong
                raise CompaniesHouseError("401 unauthorised -- check COMPANIES_HOUSE_API_KEY")
            if resp.status_code == 429:
                logger.warning("Companies House rate limit hit; stopping enrichment for this run")
                self.rate_limited = True
                raise CompaniesHouseError("429 rate limited")
            if resp.status_code >= 500:
                logger.warning("Companies House %s (%d/3)", resp.status_code, attempt)
                time.sleep(2 * attempt)
                continue
            raise CompaniesHouseError(f"HTTP {resp.status_code} for {path}")
        raise CompaniesHouseError(f"gave up on {path}")


# ---------------------------------------------------------------- parsing

def _parse_date(value: Any) -> Optional[date]:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


_NATURE_RE = re.compile(r"(ownership-of-shares|voting-rights)-(\d+)-to-(\d+)-percent")


def _describe_natures(natures: list[str]) -> str:
    parts = []
    for n in natures or []:
        m = _NATURE_RE.fullmatch(n)
        if m:
            kind = "shares" if m.group(1) == "ownership-of-shares" else "votes"
            parts.append(f"{m.group(2)}-{m.group(3)}% {kind}")
        elif n == "right-to-appoint-and-remove-directors":
            parts.append("appoints directors")
        else:
            parts.append(n.replace("-", " "))
    return ", ".join(parts[:3])


def summarise(profile: dict[str, Any], psc: Optional[dict[str, Any]],
              officers: Optional[dict[str, Any]]) -> dict[str, Any]:
    addr = profile.get("registered_office_address") or {}
    place = ", ".join(p for p in (addr.get("locality"), addr.get("postal_code", "")[:4].strip()) if p)
    pscs = []
    for item in (psc or {}).get("items", []) or []:
        if item.get("ceased_on") or item.get("ceased"):
            continue
        kind = (item.get("kind") or "")
        pscs.append({
            "name": item.get("name", ""),
            "type": "individual" if "individual" in kind else
                    "company" if "corporate" in kind else
                    "legal-person" if "legal-person" in kind else "other",
            "control": _describe_natures(item.get("natures_of_control") or []),
        })
    offs = []
    for item in (officers or {}).get("items", []) or []:
        if item.get("resigned_on"):
            continue
        offs.append({
            "name": item.get("name", ""),
            "role": item.get("officer_role", ""),
            "appointed": item.get("appointed_on", ""),
        })
    return {
        "number": profile.get("company_number", ""),
        "name": profile.get("company_name", ""),
        "status": profile.get("company_status", ""),
        "type": profile.get("type", ""),
        "incorporated": profile.get("date_of_creation", ""),
        "sic_codes": profile.get("sic_codes") or [],
        "place": place,
        "has_charges": bool(profile.get("has_charges")),
        "psc": pscs[:MAX_PSC_KEPT],
        "officers": offs[:MAX_OFFICERS_KEPT],
        "active_officer_count": len(offs),
    }


def age_years(info: dict[str, Any], today: Optional[date] = None) -> Optional[float]:
    d = _parse_date(info.get("incorporated"))
    if not d:
        return None
    return round(((today or date.today()) - d).days / 365.25, 1)


def university_linked(info: dict[str, Any]) -> bool:
    return any(
        p.get("type") in ("company", "legal-person") and _ACADEMIC_MARKER_RE.search(p.get("name", ""))
        for p in info.get("psc", [])
    )


def stage_hint(info: dict[str, Any]) -> str:
    age = age_years(info)
    if age is None:
        return ""
    if age < 1.5:
        return "very young"
    if age < 3:
        return "young"
    if age < 7:
        return "maturing"
    return "established"


def format_company_info(info: dict[str, Any]) -> str:
    """One compact line for the markdown report."""
    bits = [f"{info.get('name') or ''} ({info.get('number', '')})"]
    status = info.get("status", "")
    if status and status != "active":
        bits.append(f"**{status}**")
    inc = info.get("incorporated", "")
    if inc:
        age = age_years(info)
        hint = stage_hint(info)
        bits.append(f"inc. {inc[:7]}" + (f" ({age}y, {hint})" if age is not None else ""))
    if info.get("sic_codes"):
        bits.append("SIC " + "/".join(info["sic_codes"][:3]))
    if info.get("place"):
        bits.append(info["place"])
    pscs = info.get("psc") or []
    if pscs:
        shown = "; ".join(
            f"{p['name']}" + (f" [{p['control']}]" if p.get("control") else "") for p in pscs[:3]
        )
        tag = " 🎓 university-linked" if university_linked(info) else ""
        bits.append(f"controlled by {shown}{tag}")
    n = info.get("active_officer_count")
    if n:
        names = ", ".join(o["name"] for o in (info.get("officers") or [])[:3])
        bits.append(f"{n} active officer(s): {names}")
    return " · ".join(bits)


# ---------------------------------------------------------------- cache

def _load_cache(path: Path = CACHE_PATH) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict[str, Any], path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=1)
    tmp.replace(path)


def _fresh(entry: dict[str, Any]) -> bool:
    fetched = _parse_date(entry.get("fetched"))
    return bool(fetched) and (date.today() - fetched) <= timedelta(days=CACHE_TTL_DAYS)


# ---------------------------------------------------------------- public API

def fetch_company(client: Client, number: str) -> Optional[dict[str, Any]]:
    profile = client.get(f"/company/{number}")
    if not profile:
        return None
    psc = officers = None
    try:
        psc = client.get(f"/company/{number}/persons-with-significant-control")
        officers = client.get(f"/company/{number}/officers")
    except CompaniesHouseError as exc:
        logger.warning("Partial Companies House data for %s: %s", number, exc)
    return summarise(profile, psc, officers)


def enrich(signals: list[Signal], client: Optional[Client] = None,
           cache_path: Path = CACHE_PATH) -> int:
    """Attach `raw["company_info"] = {number: summary}` to every signal that
    has registration numbers in `raw["org_regnums"]` ({org name: number}).
    Returns the number of signals enriched. Safe to call without a key
    (returns 0)."""
    if client is None:
        key = api_key()
        if not key:
            logger.info("Companies House: no COMPANIES_HOUSE_API_KEY set; skipping enrichment")
            return 0
        client = Client(key)

    wanted: dict[str, None] = {}
    for s in signals:
        for number in (s.raw.get("org_regnums") or {}).values():
            n = normalise_number(number)
            if n:
                wanted[n] = None
    if not wanted:
        logger.info("Companies House: no registration numbers on these signals")
        return 0

    cache = _load_cache(cache_path)
    found = 0
    fetched = 0
    for number in wanted:
        entry = cache.get(number)
        if entry and _fresh(entry):
            continue
        if client.exhausted:
            break
        try:
            data = fetch_company(client, number)
        except CompaniesHouseError as exc:
            logger.warning("Companies House lookup failed for %s: %s", number, exc)
            if client.exhausted:
                break
            continue
        cache[number] = {"fetched": date.today().isoformat(), "data": data}
        fetched += 1
    _save_cache(cache, cache_path)

    for s in signals:
        infos = {}
        for number in (s.raw.get("org_regnums") or {}).values():
            n = normalise_number(number)
            data = (cache.get(n) or {}).get("data")
            if data:
                infos[n] = data
        if infos:
            s.raw["company_info"] = infos
            found += 1
    logger.info(
        "Companies House: %d companies wanted, %d fetched this run (%d API calls), %d signals enriched",
        len(wanted), fetched, client.calls, found,
    )
    return found
