from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from musearc.config.models import LibraryLayout, RuntimeConfig
from musearc.config.store import load_runtime_config, save_runtime_config
from musearc.infra.db.connection import DbManager


@dataclass(slots=True)
class LibraryContext:
    layout: LibraryLayout
    db: DbManager
    runtime_config: RuntimeConfig


def open_or_create_library(path: str | None) -> LibraryContext:
    """打开或创建一个音乐库。

    参数:
        path (str | None): 可选的库路径。如果提供，则使用该路径；否则使用配置中的路径或默认路径。

    返回:
        LibraryContext: 包含库布局、数据库和运行时配置的上下文对象。
    """
    cfg = load_runtime_config()  # 加载运行时配置

    # 确定库的根路径：优先使用提供的路径，其次使用配置中存储的路径，最后使用默认路径
    if path and path.strip():
        root = Path(path).expanduser().resolve()
    elif cfg.last_library_path:
        root = Path(cfg.last_library_path).expanduser().resolve()
    else:
        root = (Path.home() / "Music" / "MuseArcLibrary").resolve()

    layout = LibraryLayout(root=root)  # 创建库布局对象，基于根路径
    db = DbManager(layout)  # 创建数据库管理器，基于布局
    db.ensure_layout()  # 确保数据库布局存在（如创建目录）
    db.init_schema()  # 初始化数据库模式（如创建表）

    cfg.last_library_path = str(root)  # 更新配置中的上次库路径为当前根路径
    save_runtime_config(cfg)  # 保存更新后的运行时配置

    with db.session() as conn:  # 开启数据库会话
        from musearc.infra.db.repositories import LibraryRepository  # 导入库仓库模块

        repo = LibraryRepository(conn)  # 创建库仓库实例，使用数据库连接
        repo.set_meta("schema_version", "1")  # 设置元数据：数据库模式版本为1
        repo.set_meta("library_root", str(root))  # 设置元数据：库根路径为当前根路径

    return LibraryContext(layout=layout, db=db, runtime_config=cfg)  # 返回库上下文对象，包含布局、数据库和运行时配置
