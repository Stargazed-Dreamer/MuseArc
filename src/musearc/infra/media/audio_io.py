from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

import numpy as np

from .commands import MediaCommandError
from .ffmpeg_tools import ffmpeg_path


@dataclass(slots=True)
class DecodedAudio:
    samples: np.ndarray
    sample_rate: int
    channels: int


def decode_audio(
    path: Path,
    *,
    target_rate: int | None = None,
    target_layout: str = "mono",
) -> DecodedAudio:
    rate = int(target_rate or 22050)
    channels = 1 if target_layout == "mono" else 2
    ffmpeg = ffmpeg_path()
    cmd = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(path),
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=False)
    except Exception as exc:
        raise MediaCommandError(f"decode_failed:{path}:{exc}") from exc

    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="ignore").strip()
        raise MediaCommandError(f"decode_failed:{path}:{err or 'ffmpeg_return_nonzero'}")

    raw = proc.stdout or b""
    if not raw:
        return DecodedAudio(samples=np.zeros(0, dtype=np.float32), sample_rate=rate, channels=channels)

    samples = np.frombuffer(raw, dtype=np.float32)
    if channels > 1:
        try:
            samples = samples.reshape(-1, channels)
        except Exception as exc:
            raise MediaCommandError(f"decode_failed:{path}:invalid_pcm_shape:{exc}") from exc
        if target_layout == "mono":
            samples = samples.mean(axis=1)
            channels = 1
        else:
            samples = samples.reshape(-1)
    return DecodedAudio(samples=samples.astype(np.float32, copy=False), sample_rate=rate, channels=channels)

