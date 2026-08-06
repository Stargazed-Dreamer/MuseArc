from __future__ import annotations

import hashlib
from pathlib import Path

from musearc.core.hashing import sha1_text, sha256_file


def test_sha1_text_known_value():
    """sha1_text 应与 hashlib 直接计算的结果一致。"""
    text = "hello"
    expected = hashlib.sha1(text.encode("utf-8")).hexdigest()
    assert sha1_text(text) == expected


def test_sha1_text_empty_string():
    """空字符串的 SHA1 应与 hashlib 计算的空串哈希一致。"""
    expected = hashlib.sha1(b"").hexdigest()
    assert sha1_text("") == expected


def test_sha1_text_unicode():
    """Unicode 文本应使用 UTF-8 编码后计算哈希。"""
    text = "晴天 hello"
    expected = hashlib.sha1(text.encode("utf-8")).hexdigest()
    assert sha1_text(text) == expected


def test_sha1_text_ignores_invalid_utf8():
    """errors='ignore' 应保证编码不抛异常（这里用正常字符串验证行为）。"""
    # 普通字符串编码不会触发 ignore 逻辑，仅验证一致即可
    text = "normal text"
    expected = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()
    assert sha1_text(text) == expected


def test_sha256_file_matches_hashlib(tmp_path: Path):
    """sha256_file 应与 hashlib 直接计算的文件哈希一致。"""
    content = b"some binary file content for hashing\n"
    path = tmp_path / "sample.bin"
    path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert sha256_file(path) == expected


def test_sha256_file_large_chunk(tmp_path: Path):
    """使用较大的 chunk_size 应得到相同的哈希结果。"""
    content = b"x" * 5000  # 5KB，小于默认 1MB chunk
    path = tmp_path / "big.bin"
    path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert sha256_file(path, chunk_size=4096) == expected


def test_sha256_file_small_chunk(tmp_path: Path):
    """使用很小的 chunk_size 分多次读取应得到相同的哈希结果。"""
    content = bytes(range(256)) * 10  # 2560 字节
    path = tmp_path / "small_chunk.bin"
    path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    # chunk_size=1 强制逐字节读取
    assert sha256_file(path, chunk_size=1) == expected


def test_sha256_file_empty_file(tmp_path: Path):
    """空文件的 SHA256 应与 hashlib 计算的空字节哈希一致。"""
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")

    expected = hashlib.sha256(b"").hexdigest()
    assert sha256_file(path) == expected
