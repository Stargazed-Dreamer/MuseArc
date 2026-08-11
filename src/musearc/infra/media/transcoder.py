from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import av

from .commands import MediaCommandError


@dataclass(slots=True)
class ExportFormat:
    fmt: str
    bitrate: str | None = None
    sample_rate: int | None = None


def _parse_bitrate(value: str | None, default_value: int | None = None) -> int | None:
    """
    解析比特率字符串，将其转换为整数。

    参数:
        value (str | None): 要解析的比特率字符串，可能以"k"或"m"结尾，表示千比特或兆比特。
        default_value (int | None): 当输入无效时的默认值，默认为None。

    返回值:
        int | None: 解析后的比特率整数，如果解析失败则返回默认值。
    """
    # 如果输入值为空或None，直接返回默认值
    if not value:
        return default_value
    # 清理字符串：去除首尾空白并转换为小写，以便统一处理
    text = value.strip().lower()
    try:
        # 检查字符串是否以"k"结尾，表示千比特单位
        if text.endswith("k"):
            # 移除后缀"k"，转换为浮点数，乘以1000转换为整数比特率
            return int(float(text[:-1]) * 1000)
        # 检查字符串是否以"m"结尾，表示兆比特单位
        if text.endswith("m"):
            # 移除后缀"m"，转换为浮点数，乘以1000000转换为整数比特率
            return int(float(text[:-1]) * 1_000_000)
        # 如果没有单位后缀，直接将字符串转换为整数
        return int(text)
    except ValueError:
        # 如果转换过程中出现值错误（例如非数字字符串），返回默认值
        return default_value


def _iter_frames(value):
    """
    功能：将输入值转换为帧列表，确保输出始终为列表格式。
    参数：value - 输入值，可以是任意类型（如None、列表或其他单个值）。
    返回值：一个列表，如果输入为None则返回空列表，如果输入是列表则返回原列表，否则将输入值包装成单元素列表返回。
    """
    if value is None:  # 检查输入是否为None
        return []  # 输入为None时返回空列表
    if isinstance(value, list):  # 检查输入是否已经是列表类型
        return value  # 输入为列表时直接返回原列表
    return [value]  # 输入为其他类型时，将其包装成单元素列表返回


class MediaTranscoder:
    def transcode_to_opus(self, source: Path, target: Path) -> None:
        self.export_audio(
            source,
            target,
            ExportFormat(fmt="opus", bitrate="160k", sample_rate=48000),
        )

    def export_audio(self, source: Path, target: Path, options: ExportFormat) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fmt = options.fmt.lower().strip(".")
        codec = self._codec_for_format(fmt)

        try:
            with av.open(str(source)) as in_container, av.open(str(target), mode="w") as out_container:
                in_stream = None
                for stream in in_container.streams:
                    if stream.type == "audio":
                        in_stream = stream
                        break
                if in_stream is None:
                    raise MediaCommandError("no_audio_stream")

                input_rate = in_stream.codec_context.sample_rate or 48000
                output_rate = options.sample_rate or input_rate
                channels = in_stream.codec_context.channels or 2
                output_layout = "stereo" if channels >= 2 else "mono"

                out_stream = out_container.add_stream(codec, rate=output_rate)
                out_stream.layout = output_layout

                bit_rate = _parse_bitrate(options.bitrate)
                if bit_rate:
                    out_stream.codec_context.bit_rate = bit_rate
                elif fmt == "opus":
                    out_stream.codec_context.bit_rate = 160_000

                out_container.metadata.update(in_container.metadata or {})

                resampler = av.AudioResampler(format="fltp", layout=output_layout, rate=output_rate)

                for frame in in_container.decode(in_stream):
                    frame.pts = None
                    for resampled in _iter_frames(resampler.resample(frame)):
                        resampled.pts = None
                        for packet in out_stream.encode(resampled):
                            out_container.mux(packet)

                for resampled in _iter_frames(resampler.resample(None)):
                    for packet in out_stream.encode(resampled):
                        out_container.mux(packet)

                for packet in out_stream.encode(None):
                    out_container.mux(packet)
        except MediaCommandError:
            raise
        except Exception as exc:  # pragma: no cover - backend specific
            raise MediaCommandError(f"transcode_failed:{source}:{exc}") from exc

    @staticmethod
    def _codec_for_format(fmt: str) -> str:
        if fmt == "opus":
            return "libopus"
        if fmt == "mp3":
            return "libmp3lame"
        if fmt == "flac":
            return "flac"
        if fmt == "wav":
            return "pcm_s16le"
        raise MediaCommandError(f"unsupported_export_format:{fmt}")

