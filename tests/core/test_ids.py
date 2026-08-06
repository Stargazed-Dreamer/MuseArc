from __future__ import annotations

import re

from musearc.core.ids import new_id


def test_new_id_starts_with_prefix():
    """new_id 返回值应以 '{prefix}_' 开头。"""
    assert new_id("trk").startswith("trk_")


def test_new_id_format():
    """new_id 返回值应匹配 '{prefix}_{32位hex}' 格式。"""
    value = new_id("lrc")
    # 形如 lrc_ + 32 个十六进制字符
    assert re.fullmatch(r"lrc_[0-9a-f]{32}", value)


def test_new_id_hex_part_length():
    """前缀之后的 hex 部分应为 32 位。"""
    value = new_id("imp")
    hex_part = value[len("imp_"):]
    assert len(hex_part) == 32


def test_new_id_unique():
    """两次调用 new_id 应返回不同的值。"""
    a = new_id("trk")
    b = new_id("trk")
    assert a != b


def test_new_id_different_prefixes():
    """不同前缀应产生不同前缀的 ID。"""
    a = new_id("trk")
    b = new_id("lrc")
    assert a.startswith("trk_")
    assert b.startswith("lrc_")


def test_new_id_empty_prefix():
    """空前缀时返回值应以 '_' 开头并紧跟 32 位 hex。"""
    value = new_id("")
    assert value.startswith("_")
    assert re.fullmatch(r"_[0-9a-f]{32}", value)
