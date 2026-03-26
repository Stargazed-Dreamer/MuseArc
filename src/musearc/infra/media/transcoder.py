from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from .commands import MediaCommandError
from .ffmpeg_tools import ffmpeg_path


@dataclass(slots=True)
class ExportFormat:
    fmt: str
    bitrate: str | None = None
    sample_rate: int | None = None


class MediaTranscoder:
    def transcode_to_opus(self, source: Path, target: Path) -> None:
        self.export_audio(source, target, ExportFormat(fmt="opus", bitrate="160k", sample_rate=48000))

    def export_audio(self, source: Path, target: Path, options: ExportFormat) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fmt = str(options.fmt or "").lower().strip(".")
        codec = self._codec_for_format(fmt)

        cmd = [
            str(ffmpeg_path()),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-map_metadata",
            "0",
            "-vn",
            "-c:a",
            codec,
        ]

        bitrate = str(options.bitrate or "").strip()
        if not bitrate and fmt == "opus":
            bitrate = "160k"
        if bitrate:
            cmd.extend(["-b:a", bitrate])
        if options.sample_rate:
            cmd.extend(["-ar", str(int(options.sample_rate))])
        cmd.append(str(target))

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except Exception as exc:
            raise MediaCommandError(f"transcode_failed:{source}:{exc}") from exc
        if proc.returncode != 0 or not target.exists():
            err = (proc.stderr or proc.stdout or "").strip()
            raise MediaCommandError(f"transcode_failed:{source}:{err or 'ffmpeg_return_nonzero'}")

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

