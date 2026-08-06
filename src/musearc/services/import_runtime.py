from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


def utc_now() -> datetime:
    return datetime.now(UTC)


def _safe_int(value, default: int = 0) -> int:
    """
    安全地将输入值转换为整数。

    如果输入值是列表、元组、集合或字典类型，直接返回默认值。
    否则，尝试将值转换为整数。如果转换失败或值为假值（如None、False等），使用0作为转换基础。
    任何异常情况下都返回默认值。

    参数:
        value: 要转换为整数的输入值。
        default: 可选的默认整数值，默认为0。

    返回值:
        int: 转换成功时返回整数，否则返回默认值。
    """
    if isinstance(value, (list, tuple, set, dict)):  # 检查值是否为集合类型，如列表、元组等
        return default  # 如果是集合类型，直接返回默认值
    try:
        return int(value or 0)  # 尝试转换：如果value是假值（如None、False、0或空字符串），则使用0作为转换基础
    except Exception:  # 捕获所有可能的异常，例如类型转换错误
        return default  # 发生异常时返回默认值


class ImportControl:
    def __init__(self):
        """初始化方法，设置线程锁、暂停事件和取消标志。

        功能：初始化实例的线程控制变量，用于管理线程的暂停和取消操作。
        参数：无（除了默认的self参数）。
        返回值：无（此方法仅初始化实例属性）。
        """
        self._lock = threading.Lock()  # 创建线程锁，用于同步访问共享资源
        self._pause_event = threading.Event()  # 创建线程事件，用于控制线程的暂停和恢复
        self._pause_event.set()  # 设置事件，表示初始状态为非暂停（可运行）
        self._cancel_requested = False  # 初始化取消请求标志为False，表示尚未请求取消
        self._cancel_mode = "keep"  # 设置取消模式为"keep"，可能表示取消时保持某些状态或资源

    def request_cancel(self, mode: str) -> None:
        normalized = mode if mode in {"keep", "rollback"} else "keep"
        with self._lock:
            self._cancel_requested = True
            self._cancel_mode = normalized
        self._pause_event.set()

    def request_pause(self) -> None:
        self._pause_event.clear()

    def request_resume(self) -> None:
        self._pause_event.set()

    def wait_if_paused(self, timeout_sec: float = 0.2) -> bool:
        return self._pause_event.wait(timeout=timeout_sec)

    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def snapshot(self) -> tuple[bool, str, bool]:
        """获取当前对象的快照状态。

        参数：无。

        返回：一个元组，包含三个元素：
            - 第一个元素是布尔值，表示是否请求取消。
            - 第二个元素是字符串，表示取消模式。
            - 第三个元素是布尔值，表示是否暂停。
        """
        with self._lock:  # 获取锁以确保线程安全，防止并发访问共享状态
            return self._cancel_requested, self._cancel_mode, self.is_paused()


@dataclass(slots=True)
class ResumeState:
    version: int
    import_batch_id: str
    source_path: str
    started_at: str
    scanned_files: int
    processed_files: int
    processed_relpaths: list[str] = field(default_factory=list)
    imported_tracks: int = 0
    duplicate_tracks: int = 0
    imported_lyrics: int = 0
    matched_lyrics: int = 0
    review_items: int = 0
    errors: list[str] = field(default_factory=list)
    file_states: list[dict] = field(default_factory=list)
    created_track_ids: list[str] = field(default_factory=list)
    created_lyrics_ids: list[str] = field(default_factory=list)
    created_storage_relpaths: list[str] = field(default_factory=list)
    soft_deleted_existing_ids: list[str] = field(default_factory=list)


def _resume_dir(library_root: Path) -> Path:
    return library_root / "manifests" / "imports" / "resume"


def _source_key(source_path: Path) -> str:
    """根据源路径生成一个唯一的键。

    功能：将源路径解析为绝对路径，转换为小写字符串，然后使用SHA-1哈希算法生成唯一的十六进制摘要。

    参数：
        source_path (Path): 源文件的路径对象。

    返回：
        str: 生成的唯一键，以十六进制字符串表示。
    """
    import hashlib  # 导入哈希库，用于生成SHA-1哈希

    text = str(source_path.resolve()).lower()  # 将路径解析为绝对路径并转换为小写字符串，确保一致性
    return hashlib.sha1(text.encode("utf-8")).hexdigest()  # 计算SHA-1哈希并返回十六进制表示


def resume_state_path(library_root: Path, source_path: Path) -> Path:
    return _resume_dir(library_root) / f"resume_{_source_key(source_path)}.json"


def save_resume_state(path: Path, state: ResumeState) -> None:
    """将导入恢复状态保存到指定文件路径。

    Args:
        path: 状态保存的目标文件路径
        state: 要保存的恢复状态对象，包含各种导入进度信息

    Returns:
        None: 此函数无返回值，直接将状态写入文件
    """
    # 确保保存路径的父目录存在，如果不存在则递归创建
    path.parent.mkdir(parents=True, exist_ok=True)

    # 将恢复状态对象转换为字典格式，准备进行JSON序列化
    payload = {
        "version": state.version,  # 状态版本号
        "import_batch_id": state.import_batch_id,  # 当前导入批次ID
        "source_path": state.source_path,  # 源文件路径
        "started_at": state.started_at,  # 导入开始时间
        "scanned_files": state.scanned_files,  # 已扫描的文件数
        "processed_files": state.processed_files,  # 已处理的文件数
        "processed_relpaths": state.processed_relpaths,  # 已处理的相对路径列表
        "imported_tracks": state.imported_tracks,  # 已导入的音轨数
        "duplicate_tracks": state.duplicate_tracks,  # 发现的重复音轨数
        "imported_lyrics": state.imported_lyrics,  # 已导入的歌词数
        "matched_lyrics": state.matched_lyrics,  # 已匹配的歌词数
        "review_items": state.review_items,  # 需要人工审核的项目
        "errors": state.errors,  # 导入过程中遇到的错误
        "file_states": state.file_states,  # 各文件的处理状态
        "created_track_ids": state.created_track_ids,  # 新创建的音轨ID列表
        "created_lyrics_ids": state.created_lyrics_ids,  # 新创建的歌词ID列表
        "created_storage_relpaths": state.created_storage_relpaths,  # 新创建的存储相对路径
        "soft_deleted_existing_ids": state.soft_deleted_existing_ids,  # 软删除的现有记录ID
    }

    # 将字典转换为JSON格式并写入文件
    # ensure_ascii=False允许输出中文等非ASCII字符，separators压缩JSON格式减少文件大小
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def load_resume_state(path: Path) -> ResumeState | None:
    """从指定路径的JSON文件中加载恢复状态，并将其解析为ResumeState对象。

    功能：读取JSON文件，解析内容，并构造ResumeState对象。如果文件不存在，返回None。
    参数：path (Path) - JSON文件的路径。
    返回值：ResumeState | None - 解析后的ResumeState对象，如果文件不存在则返回None。
    """
    if not path.exists():  # 检查文件是否存在，如果不存在则返回None
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))  # 读取文件内容并解析JSON字符串为字典
    return ResumeState(
        version=_safe_int(payload.get("version", 1), 1),  # 安全获取版本号，若不存在或无效则使用默认值1
        import_batch_id=str(payload["import_batch_id"]),  # 导入批次ID，转换为字符串
        source_path=str(payload["source_path"]),  # 源路径，转换为字符串
        started_at=str(payload["started_at"]),  # 开始时间，转换为字符串
        scanned_files=_safe_int(payload.get("scanned_files", 0), 0),  # 安全获取已扫描文件数，默认0
        processed_files=_safe_int(payload.get("processed_files", 0), 0),  # 安全获取已处理文件数，默认0
        processed_relpaths=list(payload.get("processed_relpaths", [])),  # 已处理相对路径列表，默认空列表
        imported_tracks=_safe_int(payload.get("imported_tracks", 0), 0),  # 安全获取已导入曲目数，默认0
        duplicate_tracks=_safe_int(payload.get("duplicate_tracks", 0), 0),  # 安全获取重复曲目数，默认0
        imported_lyrics=_safe_int(payload.get("imported_lyrics", 0), 0),  # 安全获取已导入歌词数，默认0
        matched_lyrics=_safe_int(payload.get("matched_lyrics", 0), 0),  # 安全获取匹配歌词数，默认0
        review_items=_safe_int(payload.get("review_items", 0), 0),  # 安全获取待审核项数，默认0
        errors=list(payload.get("errors", [])),  # 错误列表，默认空列表
        file_states=list(payload.get("file_states", [])),  # 文件状态列表，默认空列表
        created_track_ids=list(payload.get("created_track_ids", [])),  # 创建的曲目ID列表，默认空列表
        created_lyrics_ids=list(payload.get("created_lyrics_ids", [])),  # 创建的歌词ID列表，默认空列表
        created_storage_relpaths=list(payload.get("created_storage_relpaths", [])),  # 创建的存储相对路径列表，默认空列表
        soft_deleted_existing_ids=list(payload.get("soft_deleted_existing_ids", [])),  # 软删除的现有ID列表，默认空列表
    )


def delete_resume_state(path: Path) -> None:
    """
    删除指定路径的文件。

    参数：
        path (Path): 需要删除的文件路径，类型为 pathlib.Path。

    返回：
        None: 该函数不返回任何值。
    """
    if path.exists():  # 检查文件是否存在
        path.unlink()  # 如果存在，则删除文件


def list_resume_states(library_root: Path) -> list[dict]:
    """列出指定库中所有恢复状态文件的详情。

    该函数遍历指定库根目录下的 `resume` 状态文件夹，读取所有恢复会话状态文件，
    并将关键信息整合为一个列表返回。

    参数:
        library_root (Path): 库的根目录路径。

    返回值:
        list[dict]: 包含每个恢复状态详情的字典列表。每个字典包含以下键：
            - "file" (str): 恢复状态文件的路径。
            - "import_batch_id" (str): 关联的导入批次ID。
            - "source_path" (str): 数据原始来源路径。
            - "started_at" (str): 会话开始时间。
            - "scanned_files" (int): 已扫描的文件数量。
            - "processed_files" (int): 已处理的文件数量。
            如果恢复状态文件夹不存在，则返回空列表。
    """
    # 获取存放恢复状态文件的子目录路径
    folder = _resume_dir(library_root)
    # 检查该目录是否存在，如果不存在则直接返回空列表
    if not folder.exists():
        return []
    # 初始化一个列表，用于存放处理后的状态信息
    rows: list[dict] = []
    # 遍历目录中所有匹配 'resume_*.json' 的文件，并按文件名排序以确保顺序一致
    for file in sorted(folder.glob("resume_*.json")):
        # 加载单个恢复状态文件的内容
        state = load_resume_state(file)
        # 如果状态为空或无效（例如文件损坏），则跳过该文件
        if not state:
            continue
        # 将有效的状态信息提取并添加到结果列表中
        rows.append(
            {
                "file": str(file),
                "import_batch_id": state.import_batch_id,
                "source_path": state.source_path,
                "started_at": state.started_at,
                "scanned_files": state.scanned_files,
                "processed_files": state.processed_files,
            }
        )
    # 返回包含所有有效恢复状态信息的列表
    return rows
