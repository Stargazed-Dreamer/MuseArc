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
    if not value:
        return default_value
    text = value.strip().lower()
    try:
        if text.endswith("k"):
            return int(float(text[:-1]) * 1000)
        if text.endswith("m"):
            return int(float(text[:-1]) * 1_000_000)
        return int(text)
    except ValueError:
        return default_value


def _iter_frames(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


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

