"""Tiny JSON-file state store, so each report can flag what's new since the
last run. No database needed at this scale -- this is a hobby/portfolio
pipeline running periodically, not a service.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_DIR = Path(__file__).resolve().parent
DEFAULT_STATE_PATH = STATE_DIR / "history.json"
DEFAULT_NAME_CACHE_PATH = STATE_DIR / "gtr_name_cache.json"


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read state file %s (%s); treating as empty", path, exc)
        return default


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp_path.replace(path)  # atomic-ish swap so a crash mid-write can't corrupt state


def load_seen_keys(path: Path = DEFAULT_STATE_PATH) -> set[str]:
    data = _load_json(path, {"seen_keys": []})
    return set(data.get("seen_keys", []))


def save_seen_keys(keys: set[str], path: Path = DEFAULT_STATE_PATH) -> None:
    _save_json(path, {"seen_keys": sorted(keys)})


def load_name_cache(path: Path = DEFAULT_NAME_CACHE_PATH) -> dict[str, dict[str, str]]:
    """Persistent UUID -> name cache for GtR organisations/persons, so the
    same institution appearing across hundreds of grants only ever costs one
    lookup call, ever (not once per run)."""
    return _load_json(path, {"organisations": {}, "persons": {}})


def save_name_cache(cache: dict[str, dict[str, str]], path: Path = DEFAULT_NAME_CACHE_PATH) -> None:
    _save_json(path, cache)
