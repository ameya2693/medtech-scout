# persons/ — Seed Scout, step 1

Person-centred counterpart to the company scout. Unit = a GtR PI / Co-I, keyed on their GtR person ID.
Goal: find research groups *before* they incorporate, for very small pre-seed cheques.

```bash
python run_persons.py --region london-cambridge,edinburgh   # report -> reports/persons-<date>.md (regions: london-cambridge, edinburgh, all)
python run_persons.py --probe                       # dump GtR payload key names -> state/persons_probe.json
python run_persons.py --fixture f.json              # offline
python -m unittest discover -s tests                # offline tests (synthetic payloads)
```

## What step 1 does
1. Same GtR domain searches as the scout, but **person roles are kept** (PI / Co-I; PM and student links ignored).
2. Lead organisations resolved (company vs. academic, same rules as the scout). A PI's institution = the grant's lead organisation. A Co-I's institution is **not** in the project links, so it is left unknown, not guessed.
3. Score = best grant (topic proximity, commercial vs. academic track, recency, consortium damping, Co-I at half weight) + sustained activity + PI-at-academic bonus; an **Innovate UK funder bonus** (+1.5 per grant, `INNOVATE_UK_BONUS` in `persons/score.py`) lifts product-directed funding above research-council grants; PIs on company-led grants are penalised and listed separately. Every point has a printed reason (`persons/score.py`).
4. Markdown report with funding history per person and a "new since last run" diff.

## Not done yet
- Company check (Companies House officers / PSC): step 4. Until then a listed person may already have a company.
- Patents and inventor joins: steps 2-3. Entity resolution beyond the GtR person ID arrives there.
- ORCID and employer links: unverified; run `--probe` first and read `state/persons_probe.json`.

## State
Writes only `state/persons_cache.json`, `state/persons_seen.json` (and `persons_probe.json`). It *reads* `gtr_name_cache.json` but never writes it, so it cannot conflict with the scout workflow.

## Data protection
Names and roles are public GtR data. Only name, role, institution and grants are kept. Outreach is a separate, human step.
