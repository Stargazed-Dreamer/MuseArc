from __future__ import annotations

from musearc.core.text_normalize import lrc_visible_lines, normalize_text, token_set


def test_normalize_text_none_returns_empty():
    """None 输入应返回空字符串。"""
    assert normalize_text(None) == ""


def test_normalize_text_empty_returns_empty():
    """空字符串输入应返回空字符串。"""
    assert normalize_text("") == ""


def test_normalize_text_collapses_whitespace():
    """多余空格应被压缩并去除首尾空格。"""
    assert normalize_text("  Hello  World  ") == "hello world"


def test_normalize_text_casefold():
    """大小写应被折叠（casefold）为小写形式。"""
    assert normalize_text("HELLO") == "hello"


def test_normalize_text_removes_square_brackets():
    """方括号及其内容应被移除。"""
    assert normalize_text("[Live] Song") == "song"


def test_normalize_text_removes_parentheses():
    """圆括号及其内容应被移除。"""
    assert normalize_text("(Remix) Track") == "track"


def test_normalize_text_replaces_weak_chars():
    """∅ 字符应被替换为 0。"""
    assert normalize_text("∅1") == "01"


def test_normalize_text_nfkc_normalization():
    """全角字符应通过 NFKC 归一化为半角形式。"""
    # 全角字母 ＡＢＣ 经 NFKC + casefold 后应为 "abc"
    assert normalize_text("ＡＢＣ") == "abc"


def test_normalize_text_preserves_chinese():
    """中文字符应被保留。"""
    assert normalize_text("晴天 歌") == "晴天 歌"


def test_normalize_text_combined_special_chars():
    """混合特殊字符应被统一清理。"""
    # [Live] (Remix) 多余空格 一起处理
    assert normalize_text("[Live] (Remix) Foo") == "foo"


def test_token_set_basic():
    """token_set 应返回去重后的 token 集合。"""
    assert token_set("a b a") == {"a", "b"}


def test_token_set_empty_input():
    """空字符串输入应返回空集合。"""
    assert token_set("") == set()


def test_token_set_none_input():
    """None 输入应返回空集合。"""
    assert token_set(None) == set()


def test_token_set_returns_set_type():
    """token_set 返回值应为 set 类型。"""
    assert isinstance(token_set("hello world"), set)


def test_token_set_normalizes_input():
    """token_set 应先归一化再分词。"""
    # 大小写折叠 + 方括号移除后分词
    assert token_set("[Live] HELLO hello") == {"hello"}


def test_lrc_visible_lines_strips_timestamps():
    """应去除 [mm:ss] 时间戳并返回可见歌词行。"""
    text = "[00:01]hello\n[00:02]world\n"
    assert lrc_visible_lines(text) == ["hello", "world"]


def test_lrc_visible_lines_skips_empty_lines():
    """空行（含仅有时间戳的行）应被跳过。"""
    text = "[00:01]hello\n[00:02]\n\n[00:03]world\n"
    assert lrc_visible_lines(text) == ["hello", "world"]


def test_lrc_visible_lines_default_max_lines():
    """默认 max_lines=10 时，超过 10 行的可见内容应被截断为 10 行。"""
    # 构造 15 行带时间戳的歌词
    lines = [f"[00:{i:02d}]line{i}" for i in range(15)]
    text = "\n".join(lines)
    result = lrc_visible_lines(text)
    assert len(result) == 10
    assert result[0] == "line0"
    assert result[9] == "line9"


def test_lrc_visible_lines_custom_max_lines():
    """自定义 max_lines 应正确截断。"""
    lines = [f"[00:{i:02d}]line{i}" for i in range(5)]
    text = "\n".join(lines)
    result = lrc_visible_lines(text, max_lines=3)
    assert len(result) == 3
    assert result == ["line0", "line1", "line2"]


def test_lrc_visible_lines_empty_text():
    """空文本应返回空列表。"""
    assert lrc_visible_lines("") == []
