"""Scoring for individual signals.

Redesigned around three explicit priorities (previously the score was
dominated by funder name and target-country presence, which turned out to
reward the wrong things -- see the conversation history that led to this):

1. TOPIC PROXIMITY is now the primary driver: how strongly a signal's text
   actually matches the domain (number/specificity of matched terms), not
   just a binary "did it match at all".
2. COMPANY vs. UNIVERSITY: a real organisation (Ltd/GmbH/SA/etc.) receiving
   funding scores well above a university receiving funding. This directly
   answers "which companies are getting funded", the actual question this
   tool exists to answer.
3. COMMERCIAL vs. ACADEMIC grant track: GtR's grantCategory field lets us
   tell a Knowledge Transfer Partnership or Innovate UK R&D grant (money
   headed toward a product/company) apart from a Studentship or Fellowship
   (money headed toward training a person). The former is weighted well
   above the latter.

Funder name and target-country presence are now MINOR tie-breakers, not
dominant factors -- they used to be the main thing separating signals, which
meant location and funding body were doing the differentiating instead of
actual topic/commercial relevance, the opposite of what this tool should do.

Every point added still has a printed reason, so the report can show its
working rather than handing over an opaque number.
"""

from __future__ import annotations

from datetime import date

from core.domain import PRIORITY_FUNDERS, STRONG_TERMS, any_company_org, grant_category_track
from core.models import Signal, SignalType

# How recent is "recent"? Translational grants stay relevant signal for a
# few years; patents for longer (filing-to-grant lag is itself informative).
RECENCY_WINDOWS_DAYS = {
    SignalType.GRANT: 3 * 365,
    SignalType.PATENT: 5 * 365,
    SignalType.INCORPORATION: 2 * 365,
    SignalType.FUNDING_ROUND: 2 * 365,
}

# Base score by type is now a small baseline, not a major differentiator --
# topic proximity and the company/commercial-track bonuses below do most of
# the actual ranking work.
BASE_SCORE_BY_TYPE = {
    SignalType.GRANT: 2.0,
    SignalType.PATENT: 2.5,
    SignalType.INCORPORATION: 1.5,
    SignalType.FUNDING_ROUND: 1.0,
}

# --- Primary drivers ---------------------------------------------------------

TOPIC_TERM_WEIGHT = 0.5     # per distinct matched domain term
TOPIC_TERM_CAP = 6          # caps so one keyword-stuffed abstract can't dominate purely on volume
STRONG_TERM_BONUS = 1.0     # extra credit when a STRONG_TERMS phrase fired directly (higher precision than a platform+application pair)

LARGE_CONSORTIUM_ORGS = 8    # CORDIS projects with more partners than this get a damped company bonus
LARGE_CONSORTIUM_FACTOR = 0.4
COMPANY_ORG_BONUS = 2.5     # "which companies are getting funded" -- the explicit priority

COMMERCIAL_GRANT_BONUS = 2.0    # KTP / Grant for R&D / Innovate UK-style: money headed toward a product/company
ACADEMIC_GRANT_PENALTY = -1.5   # Studentship / Fellowship / Training Grant: money headed toward training a person, not a company

# --- Minor tie-breakers (demoted from their previous dominant role) --------

PRIORITY_FUNDER_BOOST = 0.3
TARGET_COUNTRY_BOOST = 0.3
MULTI_INVESTIGATOR_BONUS = 0.3

_STRONG_TERMS_LOWER = {t.lower() for t in STRONG_TERMS}


def _topic_proximity_score(signal: Signal) -> tuple[float, str]:
    terms = signal.raw.get("matched_terms", [])
    n = min(len(terms), TOPIC_TERM_CAP)
    score = n * TOPIC_TERM_WEIGHT
    reason = f"topic proximity: {n} matched term(s) (+{score:.2f})"
    if any(t in _STRONG_TERMS_LOWER for t in terms):
        score += STRONG_TERM_BONUS
        reason += f", strong-term match (+{STRONG_TERM_BONUS:.2f})"
    return score, reason


def score_signal(signal: Signal, *, today: date | None = None) -> Signal:
    """Score a single signal in place and return it (for easy chaining).

    Safe to call more than once on the same signal (idempotent): the first
    call snapshots the source parser's original reasons (domain match,
    funder, grant category) into `raw["_source_reasons"]`, and every call
    rebuilds `score_reasons` from that snapshot rather than accumulating on
    top of a previous scoring pass's computed additions. This matters
    because organisation names aren't resolved until AFTER the first scoring
    pass (see run.py) -- the company-vs-university bonus below can only
    fire once real names are in, so signals get scored twice: once to pick
    a resolution candidate pool, once for real after resolve_names runs.
    """
    today = today or date.today()
    score = BASE_SCORE_BY_TYPE.get(signal.signal_type, 1.0)

    if "_source_reasons" not in signal.raw:
        signal.raw["_source_reasons"] = list(signal.score_reasons)
    reasons = list(signal.raw["_source_reasons"])

    topic_score, topic_reason = _topic_proximity_score(signal)
    score += topic_score
    reasons.append(topic_reason)

    # Recency decay: full marks inside the window, linear falloff to zero at
    # 2x window. A negative age (future-dated signal -- seen recurring in
    # GtR data, e.g. a project "starting" in 2028 during a 2026 run) is
    # flagged as a data-quality issue and capped rather than rewarded as if
    # it were genuinely fresh -- the old logic silently gave these full
    # marks, which was wrong.
    if signal.signal_date:
        age_days = (today - signal.signal_date).days
        if age_days < 0:
            recency_factor = 0.5
            reasons.append(
                f"future-dated signal (age {age_days}d) -- likely a data-quality issue "
                f"upstream, recency capped at {recency_factor:.2f} rather than treated as fresh"
            )
        else:
            window = RECENCY_WINDOWS_DAYS.get(signal.signal_type, 3 * 365)
            if age_days <= window:
                recency_factor = 1.0
            elif age_days <= 2 * window:
                recency_factor = 1.0 - (age_days - window) / window
            else:
                recency_factor = 0.0
            reasons.append(f"recency factor {recency_factor:.2f} (age {age_days}d)")
        score *= max(recency_factor, 0.0)
    else:
        score *= 0.7
        reasons.append("no date available (0.7x)")

    # Company vs. university -- direct priority, per project direction.
    # Two signals, either sufficient: a Companies House regNumber (ground
    # truth from GtR, catches names with no Ltd/Limited/etc suffix -- e.g.
    # "VETERINARY ISOTHERMAL MOLECULAR DIAGNOSTICS", which the name
    # heuristic alone missed) OR the name-suffix heuristic (works for CORDIS
    # too, which has no regNumber equivalent).
    if signal.raw.get("org_has_regnum") or any_company_org(signal.organisations):
        bonus = COMPANY_ORG_BONUS
        label = "company organisation present"
        # A company somewhere in a 20-partner CORDIS consortium is weak evidence
        # that the project is company-led, so damp the bonus.
        if signal.source == "cordis" and len(signal.organisations) > LARGE_CONSORTIUM_ORGS:
            bonus = COMPANY_ORG_BONUS * LARGE_CONSORTIUM_FACTOR
            label = f"company in large consortium ({len(signal.organisations)} orgs)"
        score += bonus
        reasons.append(f"{label} (+{bonus:.2f})")

    # Commercial vs. academic grant track. Only fires for signals that carry
    # a grantCategory (currently GtR only -- CORDIS doesn't expose an
    # equivalent field in the bulk export we parse).
    category = signal.raw.get("grant_category", "")
    track = grant_category_track(category)
    if track == "commercial":
        score += COMMERCIAL_GRANT_BONUS
        reasons.append(f"commercial/spin-out grant track ({category!r}) (+{COMMERCIAL_GRANT_BONUS:.2f})")
    elif track == "academic":
        score += ACADEMIC_GRANT_PENALTY
        reasons.append(f"academic-track grant ({category!r}) ({ACADEMIC_GRANT_PENALTY:+.2f})")

    # Funder name and target-country presence: now minor tie-breakers only
    # (previously the dominant factors -- see module docstring for why that
    # was the wrong emphasis).
    for reason in signal.score_reasons:
        if reason.startswith("funder:"):
            funder = reason.split(":", 1)[1].strip()
            if funder in PRIORITY_FUNDERS:
                score += PRIORITY_FUNDER_BOOST
                reasons.append(f"known UK funder tie-breaker (+{PRIORITY_FUNDER_BOOST:.2f})")
            break
    if any(r.startswith("target country present:") for r in signal.score_reasons):
        score += TARGET_COUNTRY_BOOST
        reasons.append(f"target-country tie-breaker (+{TARGET_COUNTRY_BOOST:.2f})")

    # Multiple named people/orgs suggests a more established, findable group
    # rather than a one-line stub record. Scoring runs before name resolution
    # (see run.py), so count from the raw person UUIDs GtR gave us up front
    # rather than the (still-empty at this point) resolved `people` list.
    person_count = len(signal.raw.get("person_ids", [])) or len(signal.people)
    if person_count >= 2:
        score += MULTI_INVESTIGATOR_BONUS
        reasons.append(f"multiple named investigators (+{MULTI_INVESTIGATOR_BONUS:.2f})")

    signal.score = round(score, 2)
    signal.score_reasons = reasons
    return signal


def score_signals(signals: list[Signal], *, today: date | None = None) -> list[Signal]:
    for s in signals:
        score_signal(s, today=today)
    return sorted(signals, key=lambda s: s.score, reverse=True)
