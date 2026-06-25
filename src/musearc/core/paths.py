from __future__ import annotations

from pathlib import Path


def shard_relpath(prefix: str, entity_id: str, suffix: str) -> str:
    """根据实体ID生成分片相对路径。

    Args:
        prefix (str): 路径前缀。
        entity_id (str): 实体ID字符串，可能包含下划线。
        suffix (str): 文件后缀。

    Returns:
        str: 生成的分片相对路径，格式为 '{prefix}/{shard}/{entity_id}.{suffix}'。
    """
    tail = entity_id.split("_", 1)[-1]  # 从entity_id中提取下划线之后的部分
    shard = tail[:2]  # 取提取部分的前两个字符作为分片标识
    return f"{prefix}/{shard}/{entity_id}.{suffix}"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
