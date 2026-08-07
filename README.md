# Medtech Venture Scout

A signal-funnel pipeline for finding early-stage UK diagnostics / IVD /
microfluidics ventures before they're on anyone's radar -- built on free,
official data sources rather than scraping university websites.

The bet: by the time a spinout shows up on Beauhurst or Crunchbase, every VC
analyst already knows about it. The edge is upstream -- catching translational
grant and patent signal in a specific niche you can actually judge the science
of, before incorporation.

## Status: Phase 1 (UK, live) + Phase 1b (EU, needs one verification step)

Phase 1 wires up **UKRI Gateway to Research** (UK grants) end-to-end -- live,
tested against real data, working. Phase 1b adds **CORDIS** (EU Horizon
Europe grants), covering every EU member state plus associated countries --
including France and Switzerland specifically, both re-associated/associated
as of 2025. Same pipeline, same domain filter, same scoring -- just a second
source merged in before scoring happens. CORDIS's parser has NOT been
live-tested yet (see "CORDIS: needs one verification step" below) -- please
run it and paste back the result before relying on it.

```
medtech_scout/
├── run.py                  # CLI entrypoint -- start here
├── requirements.txt
├── .env.example             # copy to .env and fill in (Claude key optional for Phase 1)
├── core/
│   ├── models.py            # Signal / Venture dataclasses
│   ├── domain.py            # THE knob for what counts as "in scope" -- keywords, CPC classes
│   └── score.py             # scoring: signal type, recency, funder priority, target-country
├── sources/
│   ├── gtr.py                # Gateway to Research API client (UK, live)
│   └── cordis.py             # CORDIS bulk-export client (EU, needs verification -- see below)
├── synth/
│   └── claude.py             # Claude API call for analyst commentary (optional)
├── report/
│   └── markdown.py           # assembles the .md report
├── state/
│   ├── store.py              # JSON store: seen-signal keys + GtR org/person name cache
│   ├── history.json          # created on first run
│   └── gtr_name_cache.json   # created on first run; UUID->name cache, grows over time
└── reports/                  # created on first run; dated .md reports land here
```

## Setup

```bash
cd medtech_scout
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
cp .env.example .env
# edit .env: add ANTHROPIC_API_KEY if you want Claude's analyst commentary
```

## Run

```bash
python run.py                    # full run: fetch (GtR + CORDIS), score, report, update state
python run.py -v                 # verbose/debug logging
python run.py --no-claude        # skip Claude synthesis even if a key is set
python run.py --no-cordis        # GtR (UK) only, skip the EU Horizon fetch
python run.py --dry-run          # don't mark today's signals as "seen" (useful while testing)
python run.py --limit 30         # cap report size
python run.py --out custom.md    # custom output path
```

First run: everything will show as "new" (no prior state exists yet). Re-run
with `--dry-run` while you're iterating so you don't burn through the
"new since last run" diff before you're ready to rely on it.

Output lands in `reports/medtech-scout-<date>.md` by default.

## Design notes

- **`core/domain.py` is the one file to edit** if you want to widen or narrow
  the niche (add keywords, add CPC classes for when Phase 2 lands). Matching
  requires either a strong single term, or a platform term *and* an
  application term together -- this is deliberately precision-biased so the
  report stays worth reading.
- **Every score has printed reasons** (`score_reasons` on each `Signal`) --
  no black-box ranking.
- **Claude never invents facts.** `synth/claude.py` only ever gets the
  structured signal data you already have; its job is connective analyst
  prose ("why this is worth a look", "what to watch for next"), not sourcing
  new information. If the API key is missing or the call fails, the report
  still generates in full with templated one-liners instead -- degraded, not
  broken.
- **State is a flat JSON file** (`state/history.json`), not a database. Fine
  at this scale; revisit if this ever needs to run for many domains at once.

## CORDIS: needs one verification step before you rely on it

Unlike GtR, CORDIS has no simple "search by keyword" REST endpoint -- the
only no-login, no-API-key route is a monthly bulk zip export. `sources/cordis.py`
was built against CORDIS's own documentation plus several independent
third-party parsing scripts that all agree on the shape (a `project.csv` +
an `organization.csv` inside a zip, semicolon-delimited, joined by project ID
-- with organisation names and countries included directly, unlike GtR which
needed a second lookup call per UUID). But the **exact zip filename/URL for
the Horizon Europe export was not confirmed live** -- this sandbox can't
reach `cordis.europa.eu` to test it, same situation GtR was in before you
ran it the first time.

To verify:

```bash
python run.py -v --no-claude
```

Watch the CORDIS section of the log. Three outcomes:

1. **A candidate URL succeeds and signals come back looking right**
   (real titles, real organisation names with country codes) -- great,
   nothing to do.
2. **All three candidate URLs return 404/403** -- visit
   https://data.europa.eu/data/datasets/cordis-eu-research-projects-under-horizon-europe-2021-2027,
   click "Download all Horizon Europe projects", and paste back the real
   URL it gives you (or the filename, if it downloads directly) so
   `HORIZON_ZIP_CANDIDATES` in `sources/cordis.py` can be corrected.
3. **A URL succeeds but parsing fails** (`CordisClientError` mentioning
   missing columns or unexpected zip members) -- the error message includes
   the *actual* file names inside the zip, or the *actual* CSV header row,
   whichever step failed. Paste that back and the column-name mapping in
   `PROJECT_COLUMN_CANDIDATES` / `ORG_COLUMN_CANDIDATES` gets fixed against
   ground truth, the same way the GtR field names got fixed earlier.

None of these outcomes should crash the run -- CORDIS failing is designed
to degrade to "0 CORDIS signals, GtR signals unaffected," not to blank the
whole report. If it does crash, that's itself a bug worth reporting.

## Roadmap (not yet built)

- **Phase 2 -- `sources/epo.py`**: EPO Open Patent Services client. Free
  developer credentials at https://developers.epo.org (non-paying tier, ~4GB/
  week). Filters by CPC class (`core.domain.CPC_CLASSES`) + applicant-type
  heuristics (university/HEI assignees) to catch pre-spinout IP protection --
  probably the single highest-signal addition, since a university patenting
  in your niche is a strong tell before any company exists. Already covers
  France and Switzerland by default -- EPO is pan-European, no extra work
  needed for that.
- **Phase 3 -- `sources/companies.py` (UK), `sources/sirene.py` (France),
  `sources/zefix.py` (Switzerland)**: incorporation-layer sources. UK via
  Companies House (free, named search); France via INSEE Sirene + INPI RNE
  (free, no key, includes names/directors -- probably the easiest of the
  three); Switzerland via Zefix (free, requires a registered API key by
  request, all 26 cantons, officer data for GmbH/Sàrl entities).
- **Phase 4 -- `core/cluster.py`**: the real differentiator. Groups signals
  across sources by institution + named people (a PI who shows up as both a
  grant holder and a patent inventor is a much stronger cluster than either
  signal alone) into `Venture` candidates, each with an aggregate score.
- **Phase 5**: scheduling (cron/launchd), and a proper "what's new" digest
  mode so periodic runs read like a briefing rather than a fresh dump each
  time.

## Verified against a live run

The GtR client is now fixed against a real payload, not just docs: `leadFunder`
is a flat string, the usable date lives on the project's `FUND` link (not the
project's own `start`/`end`, which are usually null), the RCUK grant reference
lives under `identifiers.identifier[]`, and organisation/person names require
a second lookup call per UUID (the project payload only gives UUIDs).
`sources/gtr.py` implements that lookup with a persistent cache at
`state/gtr_name_cache.json`, resolved lazily -- only for signals that survive
scoring and the `--limit` cut, not all matches -- so repeat institutions
across hundreds of grants cost one lookup, ever, not one per grant per run.

## Known open item: the page cap is being hit on every search term

On the first live run, every one of the 6 domain search terms hit the
`MAX_PAGES_PER_QUERY` cap (8 pages x 25 = 200 records) before GtR ran out of
results -- one term alone ("microfluidic diagnostic") had ~4,900 total
matches on GtR's side, because GtR's `q` full-text search isn't an exact
phrase match. That means the domain filter is doing real work narrowing
things down, but genuine matches sitting past page 8 may be getting
truncated. Options worth considering, in rough order of effort:

- Raise `MAX_PAGES_PER_QUERY` (cheap, but scales run time and API calls
  roughly linearly, and now also scales the name-resolution step for any new
  signals it surfaces)
- Narrow the search terms in `core.domain.gtr_search_terms()` to be more
  specific phrases (fewer false-positive hits per term, so true matches
  aren't buried past the page cap)
- Investigate whether GtR's API supports a relevance or date sort parameter
  to front-load the best matches -- not yet confirmed against a live
  response, so this would need the same verify-before-build approach used
  for the fields above, rather than another guess.

None of this is broken -- it's a completeness/tuning question, not a bug.
