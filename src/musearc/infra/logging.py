"""统一日志配置。

项目内多个模块使用 ``logging.getLogger(__name__)``,但缺少统一配置入口,
导致日志默认仅输出到 stderr 且无格式化。本模块提供统一初始化入口,
应在程序入口(CLI ``app/cli.py`` 与 UI ``ui/app.py`` 启动)调用一次。

使用方式::

    from musearc.infra.logging import configure_logging
    configure_logging(level="INFO")

需要落盘到音乐库目录时::

    configure_logging(level="INFO", log_file=layout.root / "manifests" / "musearc.log")
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def configure_logging(
    level: str | int = "INFO",
    *,
    log_file: Path | None = None,
    fmt: str = _DEFAULT_FORMAT,
    datefmt: str = _DEFAULT_DATEFMT,
    force: bool = False,
) -> None:
    """配置根 logger,应在程序入口调用一次。

    参数:
        level: 日志级别,可为字符串("DEBUG"/"INFO"/"WARNING"/"ERROR")或整数。
        log_file: 若指定,同时输出到该文件;否则仅输出到 stderr。
        fmt: 日志格式字符串。
        datefmt: 时间格式字符串。
        force: 是否强制重新配置(即使已配置过)。
    """
    global _configured
    if _configured and not force:
        return

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt=datefmt,
        handlers=handlers,
        force=True,
    )
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """获取 logger 的便捷封装,统一入口。"""
    return logging.getLogger(name)
