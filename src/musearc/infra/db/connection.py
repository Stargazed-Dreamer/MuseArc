from __future__ import annotations

import sqlite3
from pathlib import Path

from musearc.config.models import LibraryLayout


class DbSession:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        """
        进入上下文管理器，建立与SQLite数据库的连接并配置数据库设置。

        功能：
            连接到SQLite数据库，设置超时时间、行工厂、启用外键约束和忙碌超时。
            尝试设置WAL日志模式和同步模式以提高性能，但在只读环境中可能失败。

        参数：
            无（self为隐式参数，表示实例自身）。

        返回：
            sqlite3.Connection: 数据库连接对象。
        """
        # 连接数据库，设置超时时间为30秒
        self._conn = sqlite3.connect(self._db_path, timeout=30.0)
        # 设置行工厂为sqlite3.Row，使查询结果可按列名访问
        self._conn.row_factory = sqlite3.Row
        # 启用外键约束，确保数据完整性
        self._conn.execute("PRAGMA foreign_keys=ON")
        # 设置忙碌超时为30000毫秒，避免锁等待过长
        self._conn.execute("PRAGMA busy_timeout=30000")
        try:
            # 尝试设置WAL日志模式以提高写入性能
            self._conn.execute("PRAGMA journal_mode=WAL")
            # 设置同步模式为NORMAL，在性能和数据安全之间取得平衡
            self._conn.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            # 某些只读/受限环境可能不允许设置 WAL，失败时保留默认行为。
            pass
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._conn is None:
            return
        if exc_type is None:
            if int(self._conn.total_changes) > 0:
                self._conn.commit()
            else:
                self._conn.rollback()
        else:
            self._conn.rollback()
        self._conn.close()


class DbManager:
    def __init__(self, layout: LibraryLayout):
        self.layout = layout

    def ensure_layout(self) -> None:
        self.layout.root.mkdir(parents=True, exist_ok=True)
        self.layout.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.layout.tracks_root.mkdir(parents=True, exist_ok=True)
        self.layout.lyrics_root.mkdir(parents=True, exist_ok=True)
        self.layout.imports_root.mkdir(parents=True, exist_ok=True)
        self.layout.exports_root.mkdir(parents=True, exist_ok=True)
        self.layout.trash_root.mkdir(parents=True, exist_ok=True)

    def init_schema(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        sql = schema_path.read_text(encoding="utf-8")
        with self.session() as conn:
            # 1) migrate old existing tables first (so later index DDL won't fail)
            self._migrate_schema(conn)
            # 2) apply schema for new libraries / missing objects
            conn.executescript(sql)
            # 3) run migration again to keep idempotent behavior for future columns
            self._migrate_schema(conn)

    def session(self) -> DbSession:
        return DbSession(self.layout.db_path)

    @staticmethod
    def _migrate_schema(conn: sqlite3.Connection) -> None:
        """
        执行数据库模式的迁移和更新，确保数据库表结构符合最新要求。

        该方法会检查并添加缺失的列，创建必要的表，并修正历史数据以保持数据一致性。

        参数:
            conn (sqlite3.Connection): 数据库连接对象

        返回:
            None: 该方法不返回任何值，直接修改数据库结构。
        """

        def table_exists(table: str) -> bool:
            """检查指定的表是否存在于数据库中。"""
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
                (table,),
            ).fetchone()
            return bool(row)

        def has_column(table: str, column: str) -> bool:
            """检查指定的表中是否存在指定的列。"""
            if not table_exists(table):
                return False
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return any(str(row[1]) == column for row in rows)

        def add_column_if_missing(table: str, ddl: str, column: str) -> None:
            """如果表存在且缺少指定列，则添加该列。"""
            if not table_exists(table):
                return
            if not has_column(table, column):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

        # 为tracks表添加缺失的列，这些列用于存储文件名、语言类型、偏好等级等元数据
        add_column_if_missing("tracks", "file_name TEXT NOT NULL DEFAULT ''", "file_name")
        add_column_if_missing("tracks", "language_kind TEXT NOT NULL DEFAULT ''", "language_kind")
        add_column_if_missing("tracks", "preference_level INTEGER NOT NULL DEFAULT 5", "preference_level")
        add_column_if_missing("tracks", "source_fullpath TEXT NOT NULL DEFAULT ''", "source_fullpath")
        add_column_if_missing("tracks", "storage_format TEXT NOT NULL DEFAULT ''", "storage_format")

        # 为playlist_items表添加缺失的entry列，用于存储播放列表条目信息
        add_column_if_missing("playlist_items", "entry INTEGER NOT NULL DEFAULT -1", "entry")

        # 为lyrics表添加缺失的列，用于存储歌词元数据
        add_column_if_missing("lyrics", "lyrics_title TEXT NOT NULL DEFAULT ''", "lyrics_title")
        add_column_if_missing("lyrics", "lyrics_artist TEXT NOT NULL DEFAULT ''", "lyrics_artist")
        add_column_if_missing("lyrics", "lyrics_album TEXT NOT NULL DEFAULT ''", "lyrics_album")
        add_column_if_missing("lyrics", "lyrics_author TEXT NOT NULL DEFAULT ''", "lyrics_author")
        add_column_if_missing("lyrics", "line_count INTEGER NOT NULL DEFAULT 0", "line_count")
        add_column_if_missing("lyrics", "deleted_at TEXT", "deleted_at")

        # 为tracks表添加缺失的fingerprint_hash32列，用于存储音频指纹哈希值
        add_column_if_missing("tracks", "fingerprint_hash32 INTEGER", "fingerprint_hash32")

        # 如果playlist_items表存在，则将旧的position列数据迁移到新的entry列（仅对未初始化的条目）
        if table_exists("playlist_items"):
            conn.execute("UPDATE playlist_items SET entry = position WHERE entry < 0")

        # 确保tracks表中的偏好等级数据在有效范围内（1-10）
        if table_exists("tracks"):
            conn.execute(
                """
                UPDATE tracks
                SET preference_level = CASE
                  WHEN preference_level < 1 THEN 1
                  WHEN preference_level > 10 THEN 10
                  ELSE preference_level
                END
                """
            )
            # 更新空的存储格式字段，将其设置为小写且去除点号的源文件扩展名
            conn.execute(
                """
                UPDATE tracks
                SET storage_format = LOWER(REPLACE(source_ext, '.', ''))
                WHERE TRIM(storage_format) = ''
                """
            )

        # 创建tag_fields表，如果它尚不存在，用于存储标签字段信息
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tag_fields (
              tag_name TEXT PRIMARY KEY,
              created_at TEXT NOT NULL
            )
            """
        )
        # 插入默认的标签字段，如"备注"和"喜爱程度"（如果它们尚未存在）
        conn.execute(
            "INSERT OR IGNORE INTO tag_fields(tag_name, created_at) VALUES('备注', datetime('now'))"
        )
        conn.execute(
            "INSERT OR IGNORE INTO tag_fields(tag_name, created_at) VALUES('喜爱程度', datetime('now'))"
        )
