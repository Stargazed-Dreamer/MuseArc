from __future__ import annotations

from pathlib import Path

from musearc.core.models import ImportCandidate
from musearc.core.text_normalize import normalize_text

AUDIO_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".wma",
    ".ape",
}

LYRICS_EXTENSIONS = {".lrc"}


def scan_import_source(source_root: Path) -> tuple[list[ImportCandidate], list[ImportCandidate]]:
    """扫描指定的源目录，识别并分类其中的音频文件和歌词文件。

    Args:
        source_root (Path): 要扫描的根目录路径。

    Returns:
        tuple[list[ImportCandidate], list[ImportCandidate]]: 一个包含两个列表的元组，第一个列表包含音频文件的候选信息，第二个列表包含歌词文件的候选信息。
    """
    # 初始化两个空列表，分别用于存储音频和歌词的候选文件信息
    audio: list[ImportCandidate] = []
    lyrics: list[ImportCandidate] = []

    # 递归遍历源根目录下的所有文件和子目录中的文件
    for path in source_root.rglob("*"):
        # 跳过目录，只处理文件
        if not path.is_file():
            continue
        # 获取文件扩展名，并转为小写以便统一比较
        ext = path.suffix.lower()
        # 获取文件主名并进行标准化处理（如统一大小写、去除特殊字符等）
        stem = normalize_text(path.stem)
        # 创建候选文件信息对象
        candidate = ImportCandidate(path=path, stem_normalized=stem, ext=ext)
        # 根据文件扩展名判断文件类型并追加到对应的列表
        if ext in AUDIO_EXTENSIONS:
            audio.append(candidate)
        elif ext in LYRICS_EXTENSIONS:
            lyrics.append(candidate)

    return audio, lyrics
