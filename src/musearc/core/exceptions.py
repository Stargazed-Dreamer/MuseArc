"""MuseArc 领域异常基类与通用错误类型。

设计原则:
- 所有项目自定义异常继承自 ``MuseArcError``,便于调用方统一捕获。
- core 层无副作用,只定义异常类型,不处理异常。
- 业务层(services/app)抛出领域异常,infra 层的具体异常(如
  ``MediaCommandError``、``PlayerClientError``)均继承 ``MuseArcError``。

迁移说明:历史代码中大量使用 ``raise ValueError(...)`` / ``raise RuntimeError(...)``
区分错误,调用方只能靠字符串匹配,可维护性差。新增异常时优先使用本模块定义的类型,
逐步替换现有 ValueError/RuntimeError。
"""
from __future__ import annotations


class MuseArcError(Exception):
    """MuseArc 所有自定义异常的基类。"""


class ValidationError(MuseArcError):
    """输入参数或数据校验失败(字段非法、空值、格式不符等)。"""


class NotFoundError(MuseArcError):
    """资源不存在(歌曲/歌词/歌单/导入批次等按 ID 查询未命中)。"""


class ConflictError(MuseArcError):
    """数据冲突(哈希冲突、唯一约束违反、重复入库等)。"""


class BusinessRuleError(MuseArcError):
    """业务规则违反(操作不被允许,如删除收藏夹歌单、非法合并目标等)。"""


class MediaError(MuseArcError):
    """媒体处理错误基类(解码/编码/探测/指纹)。"""


class PlayerError(MuseArcError):
    """外部播放器通信错误。"""


class ConfigError(MuseArcError):
    """配置加载/校验失败。"""
