#!/usr/bin/env python3
"""Seed Scout (people) -- step 1: GtR funding history per person.

Finds PIs / Co-Is on domain-matching UKRI grants, scores them as pre-company
candidates, and writes reports/persons-<date>.md.

Usage:
    python run_persons.py                          # full run
    python run_persons.py --region london-cambridge
    python run_persons.py --dry-run                # don't update the 'seen' state
    python run_persons.py --probe                  # dump GtR payload key names to
                                                   # state/persons_probe.json (no report)
    python run_persons.py --fixture f.json         # offline run from a saved fixture
Domain: SCOUT_DOMAIN=medtech (default) or climate, as for run.py.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from core.domain import gtr_search_terms
from persons import pipeline, store
from persons.extract import fetch_grants, grants_from_projects
from persons.report import build_report
from sources import gtr

DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "reports"


def _shape(obj: Any, depth: int = 3) -> Any:
    """Key names / types only, never values (data minimisation)."""
    if depth == 0:
        return type(obj).__name__
    if isinstance(obj, dict):
        return {k: _shape(v, depth - 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shape(obj[0], depth - 1)] if obj else []
    return type(obj).__name__


def probe(n_projects: int = 3, n_people: int = 5) -> None:
    """Record the structure of live project and person payloads so fields such
    as ORCID or employer links can be verified before any parsing relies on them."""
    log = logging.getLogger("probe")
    term = gtr_search_terms()[0]
    projects = []
    for p in gtr.search_projects(term):
        projects.append(p)
        if len(projects) >= n_projects:
            break
    person_ids: list[str] = []
    for p in projects:
        for link in gtr._iter_links(p):
            if (link.get("rel") or "").upper() in ("PI_PER", "COI_PER"):
                person_ids.append(gtr._uuid_from_href(link.get("href", "")))
    person_shapes = []
    for pid in list(dict.fromkeys(person_ids))[:n_people]:
        person_shapes.append(_shape(gtr._get_with_retries(f"{gtr.BASE_URL}/persons/{pid}")))
    out = {
        "term": term,
        "project_shapes": [_shape(p) for p in projects],
        "person_shapes": person_shapes,
        "link_rels_seen": sorted({(l.get("rel") or "") for p in projects for l in gtr._iter_links(p)}),
    }
    store.PROBE_PATH.parent.mkdir(parents=True, exist_ok=True)
    store.PROBE_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    log.info("Wrote %s", store.PROBE_PATH)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", choices=["all", "london-cambridge"], default="all")
    ap.add_argument("--limit", type=int, default=60, help="Max people in the ranked list")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true", help="Don't update seen state or caches")
    ap.add_argument("--probe", action="store_true", help="Dump payload key names and exit")
    ap.add_argument("--fixture", type=Path, default=None, help="Offline run from a JSON fixture")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("run_persons")

    if args.probe:
        probe()
        return 0

    kwargs: dict[str, Any] = {}
    if args.fixture:
        fx = json.loads(args.fixture.read_text(encoding="utf-8"))
        grants = grants_from_projects(fx["projects"])
        cache = {"organisations": dict(fx.get("organisations", {})), "persons": dict(fx.get("persons", {}))}
        kwargs = {"fetch_org": lambda _id: None, "fetch_name": lambda _t, _id: None}
        log.info("Offline fixture: %d grants", len(grants))
    else:
        try:
            grants = fetch_grants()
        except Exception:
            log.exception("Fatal error fetching GtR grants -- aborting")
            return 1
        cache = store.load_cache()

    result = pipeline.run(grants, cache, region=args.region, limit=args.limit, **kwargs)

    seen_before = store.load_seen()
    shown_ids = {p.person_id for p in result.ranked}
    new_ids = shown_ids - seen_before

    text = build_report(
        result.ranked, result.company_linked,
        new_ids=new_ids, total_people=result.total_people, region=args.region,
    )
    out = args.out or (DEFAULT_OUT_DIR / f"persons-{date.today().isoformat()}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    log.info("Report written to %s", out)

    if not args.dry_run and not args.fixture:
        store.save_own(result.new_orgs, result.new_names)
        store.save_seen(seen_before | shown_ids)
        log.info("State updated: %d people marked seen", len(seen_before | shown_ids))

    print(f"\nDone. {len(result.ranked)} people ranked ({len(new_ids)} new), "
          f"{len(result.company_linked)} company-linked. Report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
