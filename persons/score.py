"""Pre-company scoring for people.

The scout rewards companies over universities. This tool inverts that: the
target is an *academic* PI with translational funding who has NOT yet formed
a company, so a PI whose lead organisation is already a company is penalised
and listed separately (they belong to the company-centric scout).

Design-doc signals available at step 1 (GtR only):
  2. translational / commercial-track funding, recent   -> grant value
  5. topic proximity to the domain profile              -> grant value
  3. repeat activity (proxy for commitment)             -> sustained-activity term
  4. no company yet                                     -> company-linked penalty
     (GtR lead-org evidence only; the Companies House officer/PSC check is step 4)
Patent signals (1, 3) arrive with steps 2-3.

Every point carries a printed reason so the report can show its working.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from core.domain import STRONG_TERMS
from persons.models import Person, PersonGrant

RECENCY_WINDOW_DAYS = 3 * 365
UNDATED_FACTOR = 0.7
FUTURE_DATED_FACTOR = 0.5       # future-dated GtR records recur; cap, do not reward as fresh

TOPIC_TERM_WEIGHT = 0.5
TOPIC_TERM_CAP = 6
STRONG_TERM_BONUS = 1.0
COMMERCIAL_GRANT_BONUS = 2.0    # KTP / Innovate UK R&D / feasibility style
ACADEMIC_GRANT_PENALTY = -1.5   # studentship / fellowship / training
# Funder-based, not category-based: on the first real run every university PI's grant was
# labelled "Research Grant" / "Fellowship" etc., so grantCategory alone carried no translational
# signal. Innovate UK money is product-directed by mandate, so it is weighted above research-council
# money of otherwise identical topic and age. 1.5 is roughly three matched topic terms; tune here.
INNOVATE_UK_FUNDER = "Innovate UK"
INNOVATE_UK_BONUS = 1.5
LARGE_CONSORTIUM_ORGS = 8
LARGE_CONSORTIUM_FACTOR = 0.6
CO_I_WEIGHT = 0.5               # a Co-I is weaker evidence of founder intent than the PI

REPEAT_WEIGHT = 0.4             # credit for up to REPEAT_MAX further grants
REPEAT_MAX = 2
PI_AT_ACADEMIC_BONUS = 1.0
COMPANY_LINKED_PENALTY = -3.0
LONG_HISTORY_GRANTS = 8         # proxy for the senior-PI-with-long-history pattern
LONG_HISTORY_PENALTY = -1.0

_STRONG = {t.lower() for t in STRONG_TERMS}


def _recency(start: Optional[date], today: date) -> tuple[float, str]:
    if start is None:
        return UNDATED_FACTOR, "undated (0.7x)"
    age = (today - start).days
    if age < 0:
        return FUTURE_DATED_FACTOR, f"future-dated ({age}d, capped {FUTURE_DATED_FACTOR}x)"
    if age <= RECENCY_WINDOW_DAYS:
        return 1.0, f"recent ({age}d)"
    if age <= 2 * RECENCY_WINDOW_DAYS:
        f = 1.0 - (age - RECENCY_WINDOW_DAYS) / RECENCY_WINDOW_DAYS
        return f, f"ageing ({age}d, {f:.2f}x)"
    return 0.0, f"stale ({age}d, 0x)"


def grant_value(pg: PersonGrant, today: date) -> tuple[float, str]:
    g = pg.grant
    n = min(len(g.matched_terms), TOPIC_TERM_CAP)
    value = n * TOPIC_TERM_WEIGHT
    parts = [f"topic {n} term(s) +{value:.1f}"]
    if any(t in _STRONG for t in g.matched_terms):
        value += STRONG_TERM_BONUS
        parts.append(f"strong term +{STRONG_TERM_BONUS:.1f}")
    if g.track == "commercial":
        value += COMMERCIAL_GRANT_BONUS
        parts.append(f"commercial track +{COMMERCIAL_GRANT_BONUS:.1f}")
    elif g.track == "academic":
        value += ACADEMIC_GRANT_PENALTY
        parts.append(f"academic track {ACADEMIC_GRANT_PENALTY:+.1f}")
    if g.funder == INNOVATE_UK_FUNDER:
        value += INNOVATE_UK_BONUS
        parts.append(f"Innovate UK funder +{INNOVATE_UK_BONUS:.1f}")
    if g.n_orgs > LARGE_CONSORTIUM_ORGS:
        value *= LARGE_CONSORTIUM_FACTOR
        parts.append(f"large consortium ({g.n_orgs} orgs) x{LARGE_CONSORTIUM_FACTOR}")
    factor, note = _recency(g.start, today)
    value *= factor
    parts.append(note)
    if pg.role == "Co-I":
        value *= CO_I_WEIGHT
        parts.append(f"Co-I x{CO_I_WEIGHT}")
    return value, "; ".join(parts)


def score_person(p: Person, *, today: Optional[date] = None) -> Person:
    today = today or date.today()
    reasons: list[str] = []

    valued = sorted(((grant_value(pg, today), pg) for pg in p.grants), key=lambda x: x[0][0], reverse=True)
    (best_val, best_detail), best_pg = valued[0]
    score = best_val
    reasons.append(
        f"best grant {best_val:+.2f}: {best_pg.grant.title[:70]!r} "
        f"({best_pg.role}, {best_pg.grant.category or 'no category'}) -- {best_detail}"
    )

    extra = [v for (v, _), _ in valued[1:1 + REPEAT_MAX] if v > 0]
    if extra:
        bonus = REPEAT_WEIGHT * sum(extra)
        score += bonus
        reasons.append(f"sustained activity: {len(extra)} further relevant grant(s) (+{bonus:.2f})")

    if any(pg.role == "PI" and not pg.lead_is_company for pg in p.grants):
        score += PI_AT_ACADEMIC_BONUS
        reasons.append(f"PI at an academic lead organisation (+{PI_AT_ACADEMIC_BONUS:.2f})")

    if p.company_linked:
        score += COMPANY_LINKED_PENALTY
        reasons.append(
            f"PI on a grant led by a company -- likely already inside one ({COMPANY_LINKED_PENALTY:+.2f})"
        )
    elif any(pg.role == "Co-I" and pg.lead_is_company for pg in p.grants):
        # GtR often lists a company's own staff as Co-Is, so this is ambiguous
        # (academic collaborator vs. already inside the company). Flag, don't score.
        reasons.append(
            "Co-I on a company-led project: may be an academic collaborator or company staff (0.00, check manually)"
        )

    if len(p.grants) > LONG_HISTORY_GRANTS:
        score += LONG_HISTORY_PENALTY
        reasons.append(
            f"{len(p.grants)} domain grants: long, established history ({LONG_HISTORY_PENALTY:+.2f})"
        )

    p.score = round(score, 2)
    p.score_reasons = reasons
    return p


def score_people(people: list[Person], *, today: Optional[date] = None) -> list[Person]:
    for p in people:
        score_person(p, today=today)
    return sorted(people, key=lambda p: p.score, reverse=True)
