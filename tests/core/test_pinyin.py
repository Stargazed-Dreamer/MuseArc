from __future__ import annotations

from musearc.core.pinyin import first_letter


def test_first_letter_empty_string():
    """空字符串应返回占位符 '#'。"""
    assert first_letter("") == "#"


def test_first_letter_none():
    """None 输入应返回占位符 '#'。"""
    assert first_letter(None) == "#"


def test_first_letter_lowercase_ascii():
    """小写英文字母开头应返回对应大写字母。"""
    assert first_letter("abc") == "A"


def test_first_letter_uppercase_ascii():
    """大写英文字母开头应直接返回该字母。"""
    assert first_letter("Zoo") == "Z"


def test_first_letter_lowercase_z():
    """小写 z 开头应返回大写 Z。"""
    assert first_letter("zoo") == "Z"


def test_first_letter_chinese():
    """中文字符开头应返回拼音首字母大写。"""
    assert first_letter("晴天") == "Q"


def test_first_letter_digit():
    """数字开头应返回占位符 '#'。"""
    assert first_letter("123abc") == "#"


def test_first_letter_whitespace_only():
    """仅含空白的字符串应返回占位符 '#'。"""
    assert first_letter("   ") == "#"


def test_first_letter_strips_leading_whitespace():
    """应先去除首尾空白再判断首字符。"""
    # 去除空白后首字符为 'a'，应返回 'A'
    assert first_letter("  abc") == "A"
