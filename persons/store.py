"""State for the persons tool.

Writes ONLY its own files. It reads the scout's gtr_name_cache.json (to avoid
re-fetching names and orgs the scout already resolved) but never writes it, so
the two weekly workflows cannot produce merge conflicts on the same JSON.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from state.store import STATE_DIR, _load_json, _save_json, load_name_cache

PERSONS_CACHE_PATH = STATE_DIR / "persons_cache.json"
PERSONS_SEEN_PATH = STATE_DIR / "persons_seen.json"
PROBE_PATH = STATE_DIR / "persons_probe.json"


def load_cache(own_path: Path = PERSONS_CACHE_PATH, scout_cache: dict | None = None) -> dict[str, dict[str, Any]]:
    """Scout cache overlaid with this tool's own cache (own entries win)."""
    base = scout_cache if scout_cache is not None else load_name_cache()
    own = _load_json(own_path, {"organisations": {}, "persons": {}})
    return {
        "organisations": {**base.get("organisations", {}), **own.get("organisations", {})},
        "persons": {**base.get("persons", {}), **own.get("persons", {})},
    }


def save_own(new_orgs: dict[str, Any], new_persons: dict[str, str], own_path: Path = PERSONS_CACHE_PATH) -> None:
    own = _load_json(own_path, {"organisations": {}, "persons": {}})
    own.setdefault("organisations", {}).update(new_orgs)
    own.setdefault("persons", {}).update(new_persons)
    _save_json(own_path, own)


def load_seen(path: Path = PERSONS_SEEN_PATH) -> set[str]:
    return set(_load_json(path, {"seen_ids": []}).get("seen_ids", []))


def save_seen(ids: set[str], path: Path = PERSONS_SEEN_PATH) -> None:
    _save_json(path, {"seen_ids": sorted(ids)})
