from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """
    功能：计算指定文件的SHA256哈希值。
    参数：
        path (Path): 文件路径。
        chunk_size (int, optional): 读取文件的块大小，默认为1MB（1024 * 1024字节）。
    返回值：
        str: 文件的SHA256哈希值的十六进制字符串。
    """
    digest = hashlib.sha256()  # 初始化SHA256哈希对象
    with path.open("rb") as handle:  # 以二进制读模式打开文件
        while True:  # 循环读取文件块以处理大文件
            chunk = handle.read(chunk_size)  # 读取指定大小的数据块
            if not chunk:  # 如果读取到的块为空（文件结束），则退出循环
                break
            digest.update(chunk)  # 将数据块更新到哈希计算中
    return digest.hexdigest()  # 返回哈希值的十六进制字符串表示


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()
