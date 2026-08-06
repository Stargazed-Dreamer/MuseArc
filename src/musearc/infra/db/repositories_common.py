from __future__ import annotations

"""Repository common helpers and constants."""

import json
from datetime import UTC, datetime


def _utc_now_iso() -> str:
    """Return current UTC ISO timestamp string."""
    return datetime.now(UTC).isoformat()


def _placeholders(size: int) -> str:
    """Build SQL placeholders by size."""
    return ",".join("?" for _ in range(size))


def _safe_json_loads(value: str | None) -> dict:
    """Safe json.loads that always returns a dict."""
    if not value:
        return {}
    try:
        payload = json.loads(value)
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def _normalize_tags(ext_json_text: str | None) -> tuple[dict, dict[str, str]]:
    """Normalize ext_json.tags and return (payload, tags)."""
    payload = _safe_json_loads(ext_json_text)
    tags_raw = payload.get("tags", {})
    if not isinstance(tags_raw, dict):
        tags_raw = {}
    tags: dict[str, str] = {}
    for k, v in tags_raw.items():
        key = str(k).strip()
        if not key:
            continue
        val = str(v) if v is not None else ""
        tags[key] = val
    payload["tags"] = tags
    return payload, tags
