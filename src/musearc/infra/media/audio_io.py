from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess
import tempfile
import wave

import av
import numpy as np

from .commands import MediaCommandError


@dataclass(slots=True)
class DecodedAudio:
    samples: np.ndarray
    sample_rate: int
    channels: int


def _first_audio_stream(container: av.container.InputContainer):
    for stream in container.streams:
        if stream.type == "audio":
            return stream
    raise MediaCommandError("no_audio_stream")


def _iter_frames(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def decode_audio(
    path: Path,
    *,
    target_rate: int | None = None,
    target_layout: str = "mono",
) -> DecodedAudio:
    primary_error: Exception | None = None
    try:
        with av.open(str(path), options={"fflags": "+discardcorrupt", "err_detect": "ignore_err"}) as container:
            stream = _first_audio_stream(container)
            source_rate = stream.codec_context.sample_rate or 48000
            rate = target_rate or source_rate

            resampler = av.AudioResampler(format="fltp", layout=target_layout, rate=rate)
            chunks: list[np.ndarray] = []

            for packet in container.demux(stream):
                try:
                    frames = packet.decode()
                except Exception:
                    continue
                for frame in frames:
                    try:
                        resampled_frames = _iter_frames(resampler.resample(frame))
                    except Exception:
                        continue
                    for out_frame in resampled_frames:
                        array = out_frame.to_ndarray().astype(np.float32, copy=False)
                        if array.ndim == 2:
                            if array.shape[0] == 1:
                                mono = array[0]
                            else:
                                mono = array.mean(axis=0)
                        else:
                            mono = array
                        chunks.append(mono)

            for out_frame in _iter_frames(resampler.resample(None)):
                array = out_frame.to_ndarray().astype(np.float32, copy=False)
                if array.ndim == 2:
                    if array.shape[0] == 1:
                        mono = array[0]
                    else:
                        mono = array.mean(axis=0)
                else:
                    mono = array
                chunks.append(mono)

            if chunks:
                samples = np.concatenate(chunks, axis=0)
            else:
                samples = np.zeros(0, dtype=np.float32)

            channels = 1 if target_layout == "mono" else (stream.codec_context.channels or 2)
            return DecodedAudio(samples=samples, sample_rate=rate, channels=channels)
    except Exception as exc:  # pragma: no cover - backend specific
        primary_error = exc

    fallback_rate = int(target_rate or 22050)
    fd, tmp_name = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    Path(tmp_name).unlink(missing_ok=True)
    tmp_wav = Path(tmp_name)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(path),
        "-ac",
        "1" if target_layout == "mono" else "2",
        "-ar",
        str(fallback_rate),
        "-acodec",
        "pcm_s16le",
        str(tmp_wav),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0 or not tmp_wav.exists():
            err = (proc.stderr or proc.stdout or "").strip()
            raise MediaCommandError(f"decode_failed:{path}:{primary_error};fallback:{err}")

        with wave.open(str(tmp_wav), "rb") as wav_file:
            channels = wav_file.getnchannels() or 1
            sample_rate = wav_file.getframerate() or fallback_rate
            frame_count = wav_file.getnframes()
            raw = wav_file.readframes(frame_count)

        if not raw:
            return DecodedAudio(samples=np.zeros(0, dtype=np.float32), sample_rate=sample_rate, channels=channels)

        samples_i16 = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            samples_i16 = samples_i16.reshape(-1, channels).mean(axis=1)
            channels = 1
        return DecodedAudio(samples=samples_i16, sample_rate=sample_rate, channels=channels)
    except MediaCommandError:
        raise
    except Exception as exc:
        raise MediaCommandError(f"decode_failed:{path}:{primary_error};fallback:{exc}") from exc
    finally:
        tmp_wav.unlink(missing_ok=True)
