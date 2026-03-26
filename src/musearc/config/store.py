from __future__ import annotations

import json
import os
from pathlib import Path

from .models import ImportThresholds, RuntimeConfig


def _is_writable_dir(candidate: Path) -> bool:
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except (PermissionError, OSError):
        return False


def _pick_writable_dir(candidates: list[Path]) -> Path:
    for candidate in candidates:
        if _is_writable_dir(candidate):
            return candidate
    fallback = Path.cwd() / ".musearc"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _config_dir() -> Path:
    candidates: list[Path] = []

    app_data = os.getenv("APPDATA")
    if app_data:
        candidates.append(Path(app_data) / "MuseArc")

    candidates.append(Path.home() / ".musearc")
    candidates.append(Path.cwd() / ".musearc")

    return _pick_writable_dir(candidates)


def config_path() -> Path:
    return _config_dir() / "config.json"


def load_runtime_config() -> RuntimeConfig:
    path = config_path()
    try:
        if not path.exists():
            return RuntimeConfig()
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (PermissionError, OSError, json.JSONDecodeError):
        return RuntimeConfig()

    cfg = RuntimeConfig.model_validate(payload)

    # Config migration: reset duplicate thresholds when switching to fingerprint profile v2.
    if int(payload.get("fingerprint_profile_version", 1)) < 2:
        cfg.thresholds = ImportThresholds()
        cfg.fingerprint_profile_version = 2
        save_runtime_config(cfg)

    return cfg


def save_runtime_config(cfg: RuntimeConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        cfg.model_dump_json(indent=2),
        encoding="utf-8",
    )
