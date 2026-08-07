#!/usr/bin/env python3
"""Medtech Venture Scout -- Phase 1 CLI.

Fetches grant signals from UKRI Gateway to Research (UK) and CORDIS (EU
Horizon Europe -- covers France, Switzerland, and every other EU/associated
country), filters to the diagnostics/IVD/microfluidics domain, scores them,
diffs against previous runs, optionally synthesises analyst commentary via
the Claude API, and writes a markdown report.

Usage:
    python run.py                        # full run, writes report + updates state
    python run.py --dry-run              # don't update state (re-run without marking things "seen")
    python run.py --no-claude            # skip Claude synthesis even if a key is set
    python run.py --no-cordis            # GtR (UK) only, skip the EU Horizon fetch
    python run.py --limit 40             # cap how many signals go into the report
    python run.py --out reports/foo.md   # custom output path

Requires: `pip install -r requirements.txt`
Optional: set ANTHROPIC_API_KEY in the environment (or a .env file) for
analyst commentary. Without it, the report still generates in full.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

# Load .env if python-dotenv is available; this is optional, not required.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from core.score import score_signals
from report.markdown import build_report
from sources import cordis, gtr
from state.store import DEFAULT_STATE_PATH, load_seen_keys, save_seen_keys
from synth.claude import synthesize_commentary

DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "reports"


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=None, help="Output markdown file path")
    parser.add_argument("--limit", type=int, default=60, help="Max signals to include in the report")
    parser.add_argument("--no-claude", action="store_true", help="Skip Claude synthesis")
    parser.add_argument("--no-cordis", action="store_true", help="Skip CORDIS (EU Horizon) fetch, GtR only")
    parser.add_argument("--dry-run", action="store_true", help="Don't update the seen-signals state file")
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH, help="Path to state JSON file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)
    logger = logging.getLogger("run")

    logger.info("Fetching signals from Gateway to Research...")
    try:
        signals = gtr.fetch_signals()
    except Exception:
        logger.exception("Fatal error fetching GtR signals -- aborting run")
        return 1

    if not args.no_cordis:
        logger.info("Fetching signals from CORDIS (EU Horizon Europe)...")
        try:
            cordis_signals = cordis.fetch_signals()
            signals.extend(cordis_signals)
        except Exception:
            # CORDIS is a newer, less-verified source (see sources/cordis.py) --
            # a failure here shouldn't blank out a run that still has GtR results.
            logger.exception("CORDIS fetch failed; continuing with GtR signals only")
    else:
        logger.info("Skipping CORDIS (--no-cordis)")

    if not signals:
        logger.warning("No domain-matching signals found this run. Writing an empty report.")

    logger.info("Scoring %d signals...", len(signals))
    scored = score_signals(signals)
    scored = scored[: args.limit]

    logger.info("Resolving organisation/person names for the top %d signals...", len(scored))
    try:
        gtr.resolve_names(scored)
    except Exception:
        # Name resolution is an enrichment step, not core to the report -- a
        # failure here (e.g. GtR flaking mid-run) shouldn't blank the whole run.
        logger.exception("Name resolution failed; continuing with unresolved organisations/people")

    logger.info("Diffing against previous state (%s)...", args.state_path)
    seen_before = load_seen_keys(args.state_path)
    current_keys = {s.dedup_key() for s in scored}
    new_keys = current_keys - seen_before
    logger.info("%d signals new since last run", len(new_keys))

    commentary = None
    if not args.no_claude:
        logger.info("Requesting analyst commentary from Claude...")
        commentary = synthesize_commentary(scored)
        if commentary is None:
            logger.warning("Claude commentary unavailable this run -- falling back to templated notes")
    else:
        logger.info("Skipping Claude synthesis (--no-claude)")

    logger.info("Building report...")
    report_text = build_report(scored, new_keys=new_keys, commentary=commentary)

    out_path = args.out or (DEFAULT_OUT_DIR / f"medtech-scout-{date.today().isoformat()}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_text, encoding="utf-8")
    logger.info("Report written to %s", out_path)

    if not args.dry_run:
        save_seen_keys(current_keys, args.state_path)
        logger.info("State updated: %d signal keys now marked seen", len(current_keys))
    else:
        logger.info("--dry-run set: state file not updated")

    print(f"\nDone. {len(scored)} signals scored, {len(new_keys)} new. Report: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
