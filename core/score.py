"""Scoring for individual signals.

Phase 1 scores signals independently (no cross-source clustering yet -- that
lands in Phase 4's core/cluster.py, which will combine these into Venture
scores). The scoring model is deliberately simple and legible: every point
added has a printed reason, so the report can show its working rather than
handing you an opaque number.
"""

from __future__ import annotations

from datetime import date

from core.domain import PRIORITY_FUNDERS
from core.models import Signal, SignalType

# How recent is "recent"? Translational grants stay relevant signal for a
# few years; patents for longer (filing-to-grant lag is itself informative).
RECENCY_WINDOWS_DAYS = {
    SignalType.GRANT: 3 * 365,
    SignalType.PATENT: 5 * 365,
    SignalType.INCORPORATION: 2 * 365,
    SignalType.FUNDING_ROUND: 2 * 365,
}

BASE_SCORE_BY_TYPE = {
    # Earlier-stage signals score higher: that's the point of scouting upstream.
    SignalType.GRANT: 5.0,
    SignalType.PATENT: 6.0,
    SignalType.INCORPORATION: 4.0,
    SignalType.FUNDING_ROUND: 2.0,  # by the time it's a funding round, it's not a "find" anymore
}


def score_signal(signal: Signal, *, today: date | None = None) -> Signal:
    """Score a single signal in place and return it (for easy chaining)."""
    today = today or date.today()
    score = BASE_SCORE_BY_TYPE.get(signal.signal_type, 1.0)
    reasons = list(signal.score_reasons)  # keep domain-match reasons already attached

    # Recency decay: full marks inside the window, linear falloff to zero at 2x window.
    if signal.signal_date:
        age_days = (today - signal.signal_date).days
        window = RECENCY_WINDOWS_DAYS.get(signal.signal_type, 3 * 365)
        if age_days <= window:
            recency_factor = 1.0
        elif age_days <= 2 * window:
            recency_factor = 1.0 - (age_days - window) / window
        else:
            recency_factor = 0.0
        score *= max(recency_factor, 0.0)
        reasons.append(f"recency factor {recency_factor:.2f} (age {age_days}d)")
    else:
        # No date is a mild penalty, not a disqualifier -- some sources omit it.
        score *= 0.7
        reasons.append("no date available (0.7x)")

    # Priority funder boost (grants only).
    if signal.signal_type == SignalType.GRANT:
        for reason in signal.score_reasons:
            if reason.startswith("funder:"):
                funder = reason.split(":", 1)[1].strip()
                if funder in PRIORITY_FUNDERS:
                    score += 1.5
                    reasons.append("priority-funder boost (+1.5)")
                break

    # Multiple named people/orgs suggests a more established, findable group
    # rather than a one-line stub record. Scoring runs before name resolution
    # (see run.py), so count from the raw person UUIDs GtR gave us up front
    # rather than the (still-empty at this point) resolved `people` list.
    person_count = len(signal.raw.get("person_ids", [])) or len(signal.people)
    if person_count >= 2:
        score += 0.5
        reasons.append("multiple named investigators (+0.5)")

    # Target-country boost (CORDIS only -- see sources/cordis.py TARGET_COUNTRIES).
    # A lightweight nudge, not a filter: signals from outside the target
    # countries still score and appear, just without this boost.
    if any(r.startswith("target country present:") for r in signal.score_reasons):
        score += 1.0
        reasons.append("target-country boost (+1.0)")

    signal.score = round(score, 2)
    signal.score_reasons = reasons
    return signal


def score_signals(signals: list[Signal], *, today: date | None = None) -> list[Signal]:
    for s in signals:
        score_signal(s, today=today)
    return sorted(signals, key=lambda s: s.score, reverse=True)
