from __future__ import annotations

import json
from pathlib import Path
import subprocess

from musearc.core.models import ProbeInfo

from .commands import MediaCommandError
from .ffmpeg_tools import ffprobe_path


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value, default: int | None = None) -> int | None:
    try:
        return int(value)
    except Exception:
        return default


class MediaProbe:
    def probe(self, path: Path) -> ProbeInfo:
        ffprobe = ffprobe_path()
        cmd = [
            str(ffprobe),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except Exception as exc:
            raise MediaCommandError(f"probe_failed:{path}:{exc}") from exc

        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise MediaCommandError(f"probe_failed:{path}:{err or 'ffprobe_return_nonzero'}")

        try:
            payload = json.loads(proc.stdout or "{}")
        except Exception as exc:
            raise MediaCommandError(f"probe_failed:{path}:invalid_json:{exc}") from exc

        streams = payload.get("streams") or []
        audio_stream = None
        for stream in streams:
            if isinstance(stream, dict) and str(stream.get("codec_type", "")) == "audio":
                audio_stream = stream
                break
        if not isinstance(audio_stream, dict):
            raise MediaCommandError("no_audio_stream")

        fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}
        tags = {}
        if isinstance(fmt.get("tags"), dict):
            tags.update(fmt.get("tags") or {})
        if isinstance(audio_stream.get("tags"), dict):
            tags.update(audio_stream.get("tags") or {})

        duration_sec = _safe_float(audio_stream.get("duration"), 0.0)
        if duration_sec <= 0:
            duration_sec = _safe_float(fmt.get("duration"), 0.0)

        return ProbeInfo(
            source_path=path,
            codec=str(audio_stream.get("codec_name", "") or ""),
            duration_sec=duration_sec,
            sample_rate=_safe_int(audio_stream.get("sample_rate"), None),
            channels=_safe_int(audio_stream.get("channels"), None),
            bit_rate=_safe_int(audio_stream.get("bit_rate"), _safe_int(fmt.get("bit_rate"), None)),
            title=tags.get("title"),
            artist=tags.get("artist"),
            album=tags.get("album"),
            format_name=str(fmt.get("format_name", "") or "") or None,
        )

