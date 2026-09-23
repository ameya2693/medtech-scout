"""Assembles the periodic markdown report from scored signals.

Layout: short header/summary, "new since last run", a clustered-ventures
section (Phase 4 -- companies/institutions showing up across multiple
signals or sources, the strongest kind of evidence this tool can surface),
then the full ranked table, then per-signal detail with Claude's commentary
interleaved if available. If Claude synthesis isn't available (no API key,
or the call failed), the report still renders in full using a templated
one-liner per signal -- degraded, not broken.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from core.models import Signal, Venture


def _fallback_commentary(signal: Signal) -> str:
    reasons = "; ".join(signal.score_reasons[:3])
    return f"Flagged on: {reasons}." if reasons else "Flagged by domain and recency scoring."


def _parse_claude_commentary(commentary: Optional[str], n_signals: int) -> dict[int, str]:
    if not commentary:
        return {}
    out: dict[int, str] = {}
    current_idx: Optional[int] = None
    buffer: list[str] = []

    def flush() -> None:
        if current_idx is not None and buffer:
            out[current_idx] = " ".join(buffer).strip()

    for line in commentary.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and "]" in stripped:
            try:
                idx_str = stripped[1:stripped.index("]")]
                idx = int(idx_str)
            except ValueError:
                buffer.append(stripped)
                continue
            flush()
            current_idx = idx
            buffer = [stripped[stripped.index("]") + 1:].strip()]
        elif stripped:
            buffer.append(stripped)
    flush()
    return out


def _signal_table_row(rank: int, signal: Signal, is_new: bool) -> str:
    new_marker = "🆕 " if is_new else ""
    orgs = ", ".join(signal.organisations) or "—"
    date_str = signal.signal_date.isoformat() if signal.signal_date else "—"
    title = signal.title.replace("|", "/")[:90]
    return f"| {rank} | {new_marker}{title} | {signal.signal_type.value} | {orgs[:50]} | {date_str} | {signal.score} |"


def _venture_section(ventures: list[Venture]) -> list[str]:
    multi = [v for v in ventures if len(v.signals) >= 2]
    if not multi:
        return []
    lines = [
        "## 🔗 Clustered Ventures — repeat or cross-source signal",
        "",
        "> Companies/institutions appearing across multiple grants or multiple sources -- the strongest "
        "kind of evidence this tool can surface, since one grant could be noise but sustained activity "
        "usually isn't. Clustered from the full scored candidate pool, not just the signals below, so a "
        "company can appear here even if only one of its grants made the ranked list.",
        "",
    ]
    for v in multi:
        sources = sorted({s.source for s in v.signals})
        lines.append(f"### {v.name} — {len(v.signals)} signals, aggregate score {v.score}")
        lines.append("")
        lines.append(f"- **Sources:** {', '.join(sources)}")
        lines.append(f"- **Why clustered:** {'; '.join(v.score_reasons)}")
        lines.append("- **Contributing signals:**")
        for s in v.signals:
            date_str = s.signal_date.isoformat() if s.signal_date else "date unknown"
            lines.append(f"  - [{s.source}] {s.title} ({date_str}, score {s.score})")
        lines.append("")
    return lines


def build_report(
    signals: list[Signal],
    *,
    new_keys: set[str],
    commentary: Optional[str] = None,
    report_date: Optional[date] = None,
    domain_label: str = "diagnostics / IVD / microfluidics",
    ventures: Optional[list[Venture]] = None,
) -> str:
    report_date = report_date or date.today()
    commentary_by_idx = _parse_claude_commentary(commentary, len(signals))

    lines: list[str] = []
    lines.append(f"# Medtech Venture Scout — {report_date.isoformat()}")
    lines.append("")
    lines.append(f"**Domain:** {domain_label}  ")
    lines.append(f"**Signals this run:** {len(signals)}  ")
    new_count = sum(1 for s in signals if s.dedup_key() in new_keys)
    lines.append(f"**New since last run:** {new_count}  ")
    lines.append("")
    lines.append(
        "> Scouting funnel: grants (earliest) → patents → incorporations → funding rounds (latest). "
        "Signals are ranked by topic proximity, company vs. university, and commercial vs. academic "
        "grant track — see \"score reasons\" per item."
    )
    lines.append("")

    if new_count:
        lines.append("## 🆕 New since last run")
        lines.append("")
        lines.append("| # | Title | Type | Organisation | Date | Score |")
        lines.append("|---|---|---|---|---|---|")
        rank = 1
        for s in signals:
            if s.dedup_key() in new_keys:
                lines.append(_signal_table_row(rank, s, is_new=False))
                rank += 1
        lines.append("")

    if ventures:
        lines.extend(_venture_section(ventures))

    lines.append("## Full ranked list")
    lines.append("")
    lines.append("| # | Title | Type | Organisation | Date | Score |")
    lines.append("|---|---|---|---|---|---|")
    for i, s in enumerate(signals, start=1):
        lines.append(_signal_table_row(i, s, is_new=s.dedup_key() in new_keys))
    lines.append("")

    lines.append("## Detail")
    lines.append("")
    for i, s in enumerate(signals, start=1):
        marker = " 🆕" if s.dedup_key() in new_keys else ""
        lines.append(f"### [{i}] {s.title}{marker}")
        lines.append("")
        orgs = ", ".join(s.organisations) or "unknown"
        people = ", ".join(s.people) or "unnamed"
        date_str = s.signal_date.isoformat() if s.signal_date else "unknown"
        lines.append(f"- **Type:** {s.signal_type.value}")
        lines.append(f"- **Organisation(s):** {orgs}")
        lines.append(f"- **People:** {people}")
        lines.append(f"- **Date:** {date_str}")
        lines.append(f"- **Score:** {s.score} ({'; '.join(s.score_reasons)})")
        if s.url:
            lines.append(f"- **Link:** {s.url}")
        lines.append("")
        analyst_note = commentary_by_idx.get(i) or _fallback_commentary(s)
        lines.append(analyst_note)
        lines.append("")

    lines.append("---")
    lines.append(
        f"_Generated {datetime.now().isoformat(timespec='seconds')}. "
        "Data: UKRI Gateway to Research + CORDIS (EU Horizon Europe). "
        "Cross-source clustering (Phase 4) now live; patents and incorporations land in later phases._"
    )
    return "\n".join(lines)
