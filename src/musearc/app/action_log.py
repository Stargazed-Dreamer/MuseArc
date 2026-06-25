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
    """将一条动作日志追加到日志文件中。

    该函数会读取现有的日志文件（JSON数组格式），将新的日志条目追加到数组末尾，
    然后根据 `keep` 参数的值决定保留的最新日志条数，最后将整个数组写回文件。

    Args:
        library_root (Path): 库的根目录路径，用于构建日志文件的存储路径。
        enabled (bool): 是否启用日志记录功能。如果为 False，函数将直接返回，不执行任何操作。
        message (str): 需要记录的日志消息内容。
        level (str, optional): 日志级别。默认为 "info"。
        keep (int, optional): 要保留的最近日志条数。如果设置为正整数，且当前日志总条数超过此值，
                              则只保留最新的 `keep` 条日志。默认为 10。

    Returns:
        None: 该函数没有返回值。
    """
    # 如果日志记录未启用，则直接返回，不执行任何操作
    if not enabled:
        return
    # 获取日志文件路径并确保其父目录存在
    target = log_file_path(library_root)
    target.parent.mkdir(parents=True, exist_ok=True)

    # 初始化一个空列表，用于存储从文件中读取的日志记录
    rows: list[dict] = []
    # 如果日志文件已存在
    if target.exists():
        try:
            # 尝试读取并解析文件内容为JSON数组
            rows = json.loads(target.read_text(encoding="utf-8"))
            # 如果解析结果不是列表（可能是数据损坏），则重置为空列表
            if not isinstance(rows, list):
                rows = []
        # 如果读取或解析过程中发生任何异常（如文件损坏、格式错误），则重置为空列表
        except Exception:
            rows = []

    # 构建新的日志条目，并追加到列表中
    rows.append(
        {
            "at": _utc_now_iso(),  # 使用UTC时间戳
            "level": level,        # 记录日志级别
            "message": str(message),  # 记录日志消息
        }
    )
    # 如果设置了保留条数（keep > 0）且当前条数超过了保留限制，则只保留最后的 keep 条记录
    if keep > 0 and len(rows) > keep:
        rows = rows[-keep:]
    # 将更新后的日志列表以格式化的JSON字符串写回文件
    target.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def read_action_logs(library_root: Path) -> list[dict]:
    """
    读取指定库根路径下的操作日志文件，并返回日志数据列表。

    参数:
        library_root (Path): 库的根路径。

    返回:
        list[dict]: 日志数据列表，每个元素是一个字典。如果日志文件不存在或读取失败，则返回空列表。
    """
    # 获取日志文件的完整路径
    target = log_file_path(library_root)
    # 如果日志文件不存在，直接返回空列表
    if not target.exists():
        return []
    try:
        # 读取日志文件内容并解析为JSON对象
        rows = json.loads(target.read_text(encoding="utf-8"))
        # 检查解析结果是否为列表类型
        if isinstance(rows, list):
            return rows
    except Exception:
        # 捕获任何异常（如文件读取错误或JSON解析失败），并忽略
        pass
    # 如果解析失败或结果不是列表，返回空列表
    return []

