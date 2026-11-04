#!/usr/bin/env python3
"""Seed Scout (people) -- step 1: GtR funding history per person.

Finds PIs / Co-Is on domain-matching UKRI grants, scores them as pre-company
candidates, and writes reports/persons-<date>.md.

Usage:
    python run_persons.py                          # full run
    python run_persons.py --region london-cambridge,edinburgh
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
from persons.regions import parse_regions
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


def probe(path: Path | None = None, get=None, max_person_samples: int = 8) -> dict[str, Any]:
    """Aggregate, value-free facts about live GtR payloads, to verify assumptions before relying on them.

    Records only counts, funder / category names and key names. No person names or free text.
    Answers: which funders' projects carry PI / Co-I / PM links at all; what the person payload
    contains (e.g. ORCID, links to an employing organisation).
    """
    from collections import Counter, defaultdict

    log = logging.getLogger("probe")
    get = get or gtr._get_with_retries
    path = path or store.PROBE_PATH

    projects: dict[str, dict[str, Any]] = {}
    for term in gtr_search_terms():
        try:
            data = get(f"{gtr.BASE_URL}/projects", {"q": term, "p": 1, "s": gtr.PAGE_SIZE})
        except gtr.GtRClientError as exc:
            log.warning("probe: search %r failed: %s", term, exc)
            continue
        found = data.get("project", [])
        for proj in ([found] if isinstance(found, dict) else found):
            projects[proj.get("id") or str(len(projects))] = proj
    log.info("probe: %d distinct projects sampled", len(projects))

    tracked = ("PI_PER", "COI_PER", "PM_PER", "STUDENT_PER")
    per_funder: dict[str, Counter] = defaultdict(Counter)
    categories: dict[str, Counter] = defaultdict(Counter)
    rel_sets: Counter = Counter()
    for proj in projects.values():
        funder = (proj.get("leadFunder") or "?").strip() or "?"
        rels = sorted({(l.get("rel") or "").upper() for l in gtr._iter_links(proj)})
        per_funder[funder]["projects"] += 1
        for r in tracked:
            if r in rels:
                per_funder[funder][r] += 1
        categories[funder][(proj.get("grantCategory") or "").strip() or "(none)"] += 1
        rel_sets[(funder, tuple(rels))] += 1

    # Person samples: up to 2 per (Innovate UK vs other funder, link type), taken from projects that have such a link.
    samples, taken = [], Counter()
    for proj in projects.values():
        funder = (proj.get("leadFunder") or "?").strip()
        group = "Innovate UK" if funder == "Innovate UK" else "other"
        for link in gtr._iter_links(proj):
            rel = (link.get("rel") or "").upper()
            if rel not in ("PI_PER", "COI_PER", "PM_PER") or taken[(group, rel)] >= 2:
                continue
            if len(samples) >= max_person_samples:
                break
            taken[(group, rel)] += 1
            try:
                payload = get(f"{gtr.BASE_URL}/persons/{gtr._uuid_from_href(link.get('href', ''))}", None)
            except gtr.GtRClientError as exc:
                log.warning("probe: person fetch failed: %s", exc)
                continue
            plinks = (payload.get("links") or {}).get("link", []) if isinstance(payload, dict) else []
            plinks = [plinks] if isinstance(plinks, dict) else plinks
            samples.append({
                "from_link": rel, "funder_group": group,
                "person_link_rels": sorted({(l.get("rel") or "") for l in plinks}),
                "shape": _shape(payload, 5),
            })

    def first_shape(pred):
        for proj in projects.values():
            if pred((proj.get("leadFunder") or "").strip()):
                return _shape(proj, 5)
        return None

    out = {
        "n_projects": len(projects),
        "per_funder": {f: dict(c) for f, c in sorted(per_funder.items(), key=lambda kv: -kv[1]["projects"])},
        "categories_by_funder": {f: dict(c.most_common(8)) for f, c in categories.items()},
        "link_rel_sets": [
            {"funder": f, "rels": list(r), "count": n} for (f, r), n in rel_sets.most_common(25)
        ],
        "person_samples": samples,
        "project_shape_innovate_uk": first_shape(lambda f: f == "Innovate UK"),
        "project_shape_other": first_shape(lambda f: f != "Innovate UK"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    log.info("Wrote %s", path)
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="all",
                    help="'all', or comma-separated: london-cambridge, edinburgh (e.g. london-cambridge,edinburgh)")
    ap.add_argument("--limit", type=int, default=60, help="Max people in the ranked list")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true", help="Don't update seen state or caches")
    ap.add_argument("--probe", action="store_true", help="Dump payload key names and exit")
    ap.add_argument("--fixture", type=Path, default=None, help="Offline run from a JSON fixture")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    try:
        parse_regions(args.region)
    except ValueError as exc:
        ap.error(str(exc))
    return args


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
