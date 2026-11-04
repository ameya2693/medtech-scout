"""Markdown report for the persons tool."""

from __future__ import annotations

from datetime import date
from typing import Optional

from core.domain import DOMAIN_LABEL
from persons.models import Person

MAX_HISTORY_LINES = 8


def _md(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def _inst(p: Person) -> str:
    return _md("; ".join(p.institutions[:2])) or "unknown (Co-I only)"


def _roles(p: Person) -> str:
    pi = len(p.pi_grants)
    coi = len(p.grants) - pi
    return ", ".join(x for x in (f"{pi}x PI" if pi else "", f"{coi}x Co-I" if coi else "") if x)


def _best_track(p: Person) -> str:
    tracks = {pg.grant.track for pg in p.grants}
    for t in ("commercial", "neutral", "academic"):
        if t in tracks:
            return t
    return "—"


def _history(p: Person) -> list[str]:
    ordered = sorted(p.grants, key=lambda pg: pg.grant.start or date.min, reverse=True)
    lines = []
    for pg in ordered[:MAX_HISTORY_LINES]:
        g = pg.grant
        when = g.start.strftime("%Y-%m") if g.start else "undated"
        title = _md(g.title)[:90]
        link = f"[{title}]({g.url})" if g.url else title
        lead = f" (lead: {_md(pg.lead_org_name)}{', company' if pg.lead_is_company else ''})" if pg.lead_org_name else ""
        lines.append(f"- {when} · {g.funder or '—'} · {g.category or 'no category'} · {pg.role} · {link}{lead}")
    if len(ordered) > MAX_HISTORY_LINES:
        lines.append(f"- … and {len(ordered) - MAX_HISTORY_LINES} older")
    return lines


def build_report(
    ranked: list[Person],
    company_linked: list[Person],
    *,
    new_ids: set[str],
    total_people: int,
    region: str,
    today: Optional[date] = None,
) -> str:
    today = today or date.today()
    out: list[str] = []
    out.append(f"# Seed Scout — people ({today.isoformat()})\n")
    out.append(
        f"Domain: **{DOMAIN_LABEL}** · Source: UKRI Gateway to Research (step 1 of 5) · "
        f"Region filter: **{region}**\n"
    )
    out.append(
        f"{total_people} people (PI / Co-I) found on domain-matching grants; "
        f"{len(ranked)} shown, {len(company_linked)} flagged as likely already in a company.\n"
    )
    out.append("## How to read this\n")
    out.append(
        "- Unit is a **person**, keyed on their GtR person ID (no name matching yet).\n"
        "- Higher = academic PI, recent translational funding, on-topic, no company evidence *in GtR*.\n"
        "- **Company check is not done yet**: a person shown here may still have a company. "
        "The Companies House officer/PSC check is step 4. Patents join at steps 2-3.\n"
        "- Institution comes from the grant's lead organisation and is only known for PI roles.\n"
        "- Region filter is by institution name, so approximate."
        + (" People who appear only as Co-I are excluded under a region filter because their institution is unknown." if region != "all" else "")
        + "\n"
        "- Names and roles are public GtR data; only name, role, institution and grants are kept. "
        "Outreach is a separate, human step.\n"
    )

    new = [p for p in ranked if p.person_id in new_ids]
    out.append(f"## New since last run ({len(new)})\n")
    if new:
        for p in new[:15]:
            out.append(f"- {p.name or p.person_id} — {_inst(p)} — score {p.score:.2f}")
        if len(new) > 15:
            out.append(f"- … and {len(new) - 15} more (see ranked table)")
    else:
        out.append("None.")
    out.append("")

    out.append("## Ranked people\n")
    out.append("| # | Person | Institution | Roles | Latest | Best track | Score |")
    out.append("|---|--------|-------------|-------|--------|------------|-------|")
    for i, p in enumerate(ranked, 1):
        mark = "🆕 " if p.person_id in new_ids else ""
        latest = p.last_start.isoformat() if p.last_start else "—"
        out.append(
            f"| {i} | {mark}{_md(p.name or p.person_id)} | {_inst(p)} | {_roles(p)} | "
            f"{latest} | {_best_track(p)} | {p.score:.2f} |"
        )
    out.append("")

    out.append("## Detail\n")
    for i, p in enumerate(ranked, 1):
        out.append(f"### {i}. {p.name or p.person_id} — {_inst(p)}\n")
        out.append(f"Score **{p.score:.2f}** · {_roles(p)} · company check: not yet done (step 4)\n")
        out.append("**Why flagged**")
        out.extend(f"- {r}" for r in p.score_reasons)
        out.append("\n**Funding history**")
        out.extend(_history(p))
        out.append("")

    if company_linked:
        out.append("## Likely already in a company (belongs to the main scout)\n")
        out.append("PI on a grant whose lead organisation is a company. Listed for completeness, not ranked.\n")
        for p in company_linked:
            out.append(f"- {p.name or p.person_id} — {_inst(p)} — {_roles(p)} (score {p.score:.2f})")
        out.append("")

    return "\n".join(out)
