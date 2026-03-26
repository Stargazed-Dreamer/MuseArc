from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_file_path(library_root: Path) -> Path:
    return library_root / "manifests" / "app_logs.json"


def append_action_log(
    library_root: Path,
    *,
    enabled: bool,
    message: str,
    level: str = "info",
    keep: int = 10,
) -> None:
    if not enabled:
        return
    target = log_file_path(library_root)
    target.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    if target.exists():
        try:
            rows = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                rows = []
        except Exception:
            rows = []

    rows.append(
        {
            "at": _utc_now_iso(),
            "level": level,
            "message": str(message),
        }
    )
    if keep > 0 and len(rows) > keep:
        rows = rows[-keep:]
    target.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def read_action_logs(library_root: Path) -> list[dict]:
    target = log_file_path(library_root)
    if not target.exists():
        return []
    try:
        rows = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(rows, list):
            return rows
    except Exception:
        pass
    return []

