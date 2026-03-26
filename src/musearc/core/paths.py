from __future__ import annotations

from pathlib import Path


def shard_relpath(prefix: str, entity_id: str, suffix: str) -> str:
    tail = entity_id.split("_", 1)[-1]
    shard = tail[:2]
    return f"{prefix}/{shard}/{entity_id}.{suffix}"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
