from __future__ import annotations

import json
import os
from pathlib import Path

from .models import ImportThresholds, RuntimeConfig


def _is_writable_dir(candidate: Path) -> bool:
    """
    检查指定的目录是否可写。

    参数：
        candidate (Path): 要检查的目录路径。

    返回值：
        bool: 如果目录可写则返回True，否则返回False。
    """
    try:
        candidate.mkdir(parents=True, exist_ok=True)  # 创建目录，parents=True 表示如果父目录不存在则一并创建，exist_ok=True 表示如果目录已存在则忽略错误
        probe = candidate / ".write_probe"  # 在目录下创建一个临时文件路径用于测试可写性
        probe.write_text("ok", encoding="utf-8")  # 向临时文件写入内容以验证写入权限
        probe.unlink(missing_ok=True)  # 删除临时文件，missing_ok=True 表示如果文件不存在则忽略，避免引发错误
        return True  # 所有操作成功，目录可写
    except (PermissionError, OSError):  # 捕获权限不足或操作系统相关的错误
        return False  # 出现错误，目录不可写


def _pick_writable_dir(candidates: list[Path]) -> Path:
    """选择一个可写的目录。

    遍历给定的候选目录列表，返回第一个可写的目录。如果所有候选目录都不可写，
    则在当前工作目录下创建一个名为 ".musearc" 的备用目录并返回。

    参数:
        candidates (list[Path]): 一个由 pathlib.Path 对象组成的列表，代表候选目录。

    返回:
        Path: 最终选定的、可写入的目录路径。
    """
    for candidate in candidates:  # 遍历所有候选目录
        if _is_writable_dir(candidate):  # 检查当前候选目录是否可写
            return candidate  # 找到第一个可写的目录，立即返回
    # 如果没有可写的候选目录，则执行以下备用逻辑
    fallback = Path.cwd() / ".musearc"  # 在当前工作目录下创建备用目录的路径
    # 创建备用目录，parents=True 表示如果父目录不存在也一并创建，exist_ok=True 表示如果目录已存在则忽略错误
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback  # 返回创建的备用目录


def _config_dir() -> Path:
    """获取配置目录。
    
    该函数用于确定应用程序的配置目录路径。它会检查多个候选目录位置，
    并返回第一个可写的目录路径。优先级依次为：
    1. Windows系统下的APPDATA环境变量对应目录下的"MuseArc"子目录。
    2. 用户主目录下的".musearc"隐藏目录。
    3. 当前工作目录下的".musearc"隐藏目录。
    
    Args:
        无参数。
        
    Returns:
        Path: 返回一个可写的配置目录路径。
    """
    candidates: list[Path] = []

    # 获取Windows系统的APPDATA环境变量
    app_data = os.getenv("APPDATA")
    if app_data:
        # 如果环境变量存在，将其下的"MuseArc"目录作为首选候选
        candidates.append(Path(app_data) / "MuseArc")

    # 将用户主目录下的".musearc"目录作为次选候选
    candidates.append(Path.home() / ".musearc")
    # 将当前工作目录下的".musearc"目录作为末选候选
    candidates.append(Path.cwd() / ".musearc")

    # 从所有候选目录中选择一个可写的目录并返回
    return _pick_writable_dir(candidates)


def config_path() -> Path:
    return _config_dir() / "config.json"


def load_runtime_config() -> RuntimeConfig:
    """
    从配置文件加载运行时配置。

    参数：
        无参数。

    返回：
        RuntimeConfig: 包含配置数据的RuntimeConfig对象。
        如果配置文件不存在或解析失败，则返回默认的RuntimeConfig对象。
    """
    # 获取配置文件路径
    path = config_path()
    try:
        # 检查配置文件是否存在
        if not path.exists():
            return RuntimeConfig()
        # 读取并解析JSON格式的配置文件
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (PermissionError, OSError, json.JSONDecodeError):
        # 文件权限错误、系统错误或JSON解析错误时，返回默认配置
        return RuntimeConfig()

    # 将解析后的数据验证并创建为RuntimeConfig对象
    cfg = RuntimeConfig.model_validate(payload)

    # 配置迁移：当指纹配置文件版本低于3时，重置重复阈值
    if int(payload.get("fingerprint_profile_version", 1)) < 3:
        # 使用默认阈值重置配置
        cfg.thresholds = ImportThresholds()
        # 更新配置文件版本号
        cfg.fingerprint_profile_version = 3
        # 保存迁移后的配置
        save_runtime_config(cfg)

    # 返回最终的配置对象
    return cfg


def save_runtime_config(cfg: RuntimeConfig) -> None:
    """
    功能：将给定的运行时配置保存到文件中。
    参数：cfg (RuntimeConfig): 需要保存的运行时配置对象。
    返回值：None
    """
    path = config_path()  # 获取配置文件的路径
    path.parent.mkdir(parents=True, exist_ok=True)  # 确保目录存在，如果不存在则递归创建
    path.write_text(
        cfg.model_dump_json(indent=2),  # 将配置对象序列化为JSON字符串，缩进为2个空格
        encoding="utf-8",  # 使用UTF-8编码写入文件
    )
