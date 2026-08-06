"""MuseArc 共享常量。

集中定义跨层使用的常量,避免重复定义导致的不一致。
仓储层(``infra/db/repositories_common.py``)与 Facade 层均从此导入。
"""
from __future__ import annotations

# 收藏夹歌单(系统内置,不可删除)
FAVORITES_PLAYLIST_ID = "pl_favorites"
FAVORITES_PLAYLIST_NAME = "收藏"

# 默认标签字段(首次初始化时写入 tag_fields 表)
DEFAULT_TAG_FIELD = "备注"
DEFAULT_TAG_FIELDS = ("备注", "喜爱程度")

# 偏好等级范围(对应 tracks.preference_level,1-10)
PREFERENCE_LEVEL_MIN = 1
PREFERENCE_LEVEL_MAX = 10
