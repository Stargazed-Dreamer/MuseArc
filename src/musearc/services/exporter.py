from __future__ import annotations

import re
from pathlib import Path

from musearc.infra.db.repositories import LibraryRepository
from musearc.infra.media.transcoder import ExportFormat, MediaTranscoder


def _safe_name(value: str) -> str:
    """将字符串转换为安全的文件名格式。

    功能：移除或替换文件名中的非法字符，规范化空白字符。
    参数：value (str) - 需要处理的原始字符串。
    返回：str - 处理后的安全文件名，如果结果为空则返回 "unknown"。
    """
    # 替换文件名中不允许的特殊字符（\/:*?"<>|）为下划线
    value = re.sub(r"[\\/:*?\"<>|]", "_", value)
    # 将连续的空白字符（包括空格、制表符等）替换为单个空格，并去除首尾空白
    value = re.sub(r"\s+", " ", value).strip()
    # 如果处理后字符串为空，则返回默认名称 "unknown"，否则返回原字符串
    return value or "unknown"


class ExportService:
    def __init__(self, library_root: Path):
        """初始化一个媒体库的管理实例。

        该方法是类的构造函数，用于设置实例的初始状态，建立必要的依赖关系。

        Args:
            library_root (Path): 媒体库资源的根目录路径。所有媒体文件的操作都应基于此路径进行。

        Returns:
            None: 此方法无返回值。
        """
        self.library_root = library_root  # 将传入的库根目录路径保存为实例属性
        self.transcoder = MediaTranscoder()  # 创建并初始化媒体转码器实例，作为本实例的一个组件

    def export_tracks(
        self,
        repo: LibraryRepository,
        track_ids: list[str],
        out_dir: Path,
        *,
        fmt: str,
        bitrate: str | None,
        sample_rate: int | None,
        copy_bound_lyrics: bool = False,
    ) -> list[Path]:
        """导出指定音轨到本地文件。

        此方法将音轨库中的指定音轨，根据给定的音频格式和参数导出为文件。
        它通过构建一个格式计划来统一所有音轨的输出格式，然后调用核心的导出方法。

        Args:
            repo (LibraryRepository): 包含音轨数据的音乐库仓库对象。
            track_ids (list[str]): 需要导出的音轨ID列表。
            out_dir (Path): 导出文件的输出目录路径。
            fmt (str): 导出的音频格式（如 'mp3', 'flac'）。
            bitrate (str | None): 音频比特率，例如 '320k'，可为None。
            sample_rate (int | None): 音频采样率，例如 44100，可为None。
            copy_bound_lyrics (bool): 是否同时导出与音轨关联的歌词文件，默认为False。

        Returns:
            list[Path]: 成功导出的音频文件路径列表。
        """
        # 创建一个字典，为所有指定的音轨分配相同的导出格式
        format_plan = dict.fromkeys(track_ids, fmt)
        # 调用通用导出方法，传入统一的格式计划和其他参数
        return self.export_tracks_with_plan(
            repo,
            track_ids,
            out_dir,
            format_plan=format_plan,
            bitrate=bitrate,
            sample_rate=sample_rate,
            copy_bound_lyrics=copy_bound_lyrics,
        )

    def export_tracks_with_plan(
        self,
        repo: LibraryRepository,
        track_ids: list[str],
        out_dir: Path,
        *,
        format_plan: dict[str, str],
        bitrate: str | None,
        sample_rate: int | None,
        copy_bound_lyrics: bool = False,
    ) -> list[Path]:
        """
        根据指定的格式计划导出音轨文件。

        功能：
            从音乐库中导出指定的音轨，并根据给定的格式计划转换为指定格式。如果设置了copy_bound_lyrics，则还会复制关联的歌词文件。

        参数：
            repo: LibraryRepository - 音乐库存储库实例，用于获取音轨和歌词数据
            track_ids: list[str] - 需要导出的音轨ID列表
            out_dir: Path - 导出文件的输出目录路径
            format_plan: dict[str, str] - 格式转换计划字典，键为音轨ID，值为目标格式字符串（如'mp3'、'flac'等）
            bitrate: str | None - 目标音频比特率，用于格式转换时的参数设置
            sample_rate: int | None - 目标音频采样率，用于格式转换时的参数设置
            copy_bound_lyrics: bool - 是否复制与音轨关联的歌词文件，默认为False

        返回值：
            list[Path] - 成功导出的所有文件路径列表，包括音频文件和歌词文件（如果适用）
        """
        # 根据音轨ID列表从存储库获取对应的音轨记录
        records = repo.get_tracks_by_ids(track_ids)
        # 确保输出目录存在，如果不存在则创建
        out_dir.mkdir(parents=True, exist_ok=True)
        # 初始化导出文件路径列表
        exported: list[Path] = []

        # 遍历每个音轨记录进行处理
        for record in records:
            # 构建音轨的完整源文件路径
            source = self.library_root / record["storage_relpath"]
            # 获取音轨ID，如果记录中没有则使用空字符串
            track_id = str(record.get("track_id", ""))
            # 从格式计划中获取当前音轨的目标格式，如果没有指定则默认使用"original"
            chosen_fmt = str(format_plan.get(track_id, "original") or "original").lower().strip(".")
            # 获取音轨的原始存储格式
            source_fmt = str(record.get("storage_format") or record.get("source_ext") or "").lower().strip(".")
            # 如果目标格式为空或"original"，则使用原始格式；如果原始格式也为空，则使用"bin"作为后备格式
            if chosen_fmt in {"", "original"}:
                chosen_fmt = source_fmt or "bin"

            # 使用安全名称函数生成文件名，格式为"艺术家 - 标题"
            file_name = _safe_name(f"{record['artist']} - {record['title']}")
            # 构建目标文件的完整路径，包含格式扩展名
            target = out_dir / f"{file_name}.{chosen_fmt}"
            # 如果目标格式与原始格式相同，则直接复制文件内容
            if chosen_fmt == source_fmt:
                target.write_bytes(source.read_bytes())
            else:
                # 创建格式转换选项对象
                options = ExportFormat(fmt=chosen_fmt, bitrate=bitrate, sample_rate=sample_rate)
                # 使用转码器进行音频格式转换
                self.transcoder.export_audio(source, target, options)
            # 将导出的文件路径添加到列表中
            exported.append(target)
            # 如果设置了复制关联歌词选项
            if copy_bound_lyrics:
                # 获取音轨的主要歌词记录
                lyrics = repo.primary_lyrics_for_track(track_id) or {}
                # 获取歌词文件的相对路径
                lyrics_rel = str(lyrics.get("storage_relpath", "") or "").strip()
                # 如果歌词文件路径存在
                if lyrics_rel:
                    # 构建歌词文件的完整源路径
                    lyrics_source = self.library_root / lyrics_rel
                    # 确认歌词源文件存在
                    if lyrics_source.exists():
                        # 创建歌词目标文件路径，扩展名设为".lrc"
                        lyric_target = target.with_suffix(".lrc")
                        # 读取歌词内容并写入到目标文件，使用UTF-8编码
                        lyric_target.write_text(lyrics_source.read_text(encoding="utf-8"), encoding="utf-8")

        # 返回所有导出的文件路径列表
        return exported
