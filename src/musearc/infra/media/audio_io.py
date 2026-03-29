from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


def _frame_to_mono_array(frame: av.AudioFrame) -> np.ndarray:
    array = frame.to_ndarray().astype(np.float32, copy=False)
    if array.ndim == 2:
        if array.shape[0] == 1:
            return array[0]
        return array.mean(axis=0)
    return array


def decode_audio(
    path: Path,
    *,
    target_rate: int | None = None,
    target_layout: str = "mono",
    apply_loudnorm: bool = False,
    target_lufs: float = -14.0,
) -> DecodedAudio:
    try:
        with av.open(str(path), options={"fflags": "+discardcorrupt", "err_detect": "ignore_err"}) as container:
            stream = _first_audio_stream(container)
            source_rate = stream.codec_context.sample_rate or 48000
            rate = int(target_rate or source_rate)
            layout = str(target_layout or "mono")
            chunks: list[np.ndarray] = []

            if apply_loudnorm:
                graph = av.filter.Graph()
                src = graph.add_abuffer(template=stream)
                loud = graph.add("loudnorm", args=f"I={float(target_lufs):.1f}:TP=-1.5:LRA=11")
                fmt = graph.add(
                    "aformat",
                    args=f"sample_fmts=fltp:sample_rates={rate}:channel_layouts={layout}",
                )
                sink = graph.add("abuffersink")
                src.link_to(loud)
                loud.link_to(fmt)
                fmt.link_to(sink)
                graph.configure()

                for packet in container.demux(stream):
                    try:
                        frames = packet.decode()
                    except Exception:
                        continue
                    for frame in frames:
                        try:
                            src.push(frame)
                        except Exception:
                            continue
                        while True:
                            try:
                                out = sink.pull()
                            except Exception:
                                break
                            chunks.append(_frame_to_mono_array(out))
                try:
                    src.push(None)
                except Exception:
                    pass
                while True:
                    try:
                        out = sink.pull()
                    except Exception:
                        break
                    chunks.append(_frame_to_mono_array(out))
            else:
                resampler = av.AudioResampler(format="fltp", layout=layout, rate=rate)
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
                            chunks.append(_frame_to_mono_array(out_frame))
                for out_frame in _iter_frames(resampler.resample(None)):
                    chunks.append(_frame_to_mono_array(out_frame))

            samples = np.concatenate(chunks, axis=0) if chunks else np.zeros(0, dtype=np.float32)
            channels = 1 if layout == "mono" else (stream.codec_context.channels or 2)
            return DecodedAudio(samples=samples, sample_rate=rate, channels=channels)
    except MediaCommandError:
        raise
    except Exception as exc:  # pragma: no cover - backend specific
        raise MediaCommandError(f"decode_failed:{path}:{exc}") from exc

