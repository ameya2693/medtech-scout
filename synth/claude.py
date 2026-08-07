"""Uses the Claude API to turn scored signals into short analyst commentary.

This module does NOT invent facts: it's given only the structured signal data
(title, summary, org, score, reasons) and asked to write brief connective
prose -- why a signal matters, what to watch for next -- not to add external
information it wasn't given. The markdown report assembles the factual
tables itself; Claude's job here is the "so what" layer.

Requires ANTHROPIC_API_KEY in the environment. If it's missing or the call
fails, callers should fall back to a templated (Claude-free) summary rather
than block the whole report -- see report/markdown.py.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import requests

from core.models import Signal

logger = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"  # verified current model string as of 2026-07-26
MAX_TOKENS = 1500
REQUEST_TIMEOUT = 60

SYSTEM_PROMPT = """You are a venture-scouting analyst assistant helping a bioengineer \
(PhD, microfluidics/diagnostics background) screen early-stage signals for \
promising UK medical-device and diagnostics ventures before they're widely known.

You are given a list of scored signals (grants, patents, incorporations) that \
have already been filtered to the diagnostics/IVD/microfluidics domain and \
ranked by a scoring model. Your job is ONLY to add brief analyst commentary \
on top of what's given -- never invent facts, funding amounts, people, or \
outcomes that aren't in the data provided. If you're not confident about \
something, say so or omit it.

For each signal, write 1-3 sentences covering: why this is worth a look (the \
science/technology angle, since the reader can judge that), and what the \
next signal to watch for would be (e.g. a patent filing, an incorporation, a \
funding round). Be concrete and specific to the actual title/summary given, \
not generic boilerplate. Do not use markdown headers in your output -- plain \
prose per signal, referenced by the number given."""


class ClaudeSynthesisError(RuntimeError):
    pass


def _build_user_prompt(signals: list[Signal]) -> str:
    lines = ["Here are the top scored signals for this period:\n"]
    for i, s in enumerate(signals, start=1):
        orgs = ", ".join(s.organisations) or "unknown organisation"
        people = ", ".join(s.people) or "unnamed"
        date_str = s.signal_date.isoformat() if s.signal_date else "date unknown"
        lines.append(
            f"[{i}] ({s.signal_type.value}, score {s.score}) \"{s.title}\"\n"
            f"    Organisation(s): {orgs}\n"
            f"    People: {people}\n"
            f"    Date: {date_str}\n"
            f"    Summary: {s.summary or '(no abstract available)'}\n"
        )
    lines.append(
        "\nWrite the per-signal commentary now, one short paragraph per signal, "
        "each starting with its bracketed number, e.g. '[1] ...'."
    )
    return "\n".join(lines)


def synthesize_commentary(signals: list[Signal]) -> Optional[str]:
    """Return Claude's commentary text, or None if the API call isn't
    available/fails (caller should fall back gracefully)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set; skipping Claude synthesis")
        return None
    if not signals:
        return None

    payload = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": _build_user_prompt(signals)}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    try:
        resp = requests.post(API_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.error("Claude API request failed: %s", exc)
        return None

    if resp.status_code != 200:
        logger.error("Claude API returned HTTP %s: %s", resp.status_code, resp.text[:500])
        return None

    try:
        data = resp.json()
        text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        return "\n".join(text_blocks).strip() or None
    except (ValueError, KeyError) as exc:
        logger.error("Could not parse Claude API response: %s", exc)
        return None
