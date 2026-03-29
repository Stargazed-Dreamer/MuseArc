from __future__ import annotations

from pathlib import Path

import av

from musearc.core.models import ProbeInfo

from .commands import MediaCommandError


class MediaProbe:
    def probe(self, path: Path) -> ProbeInfo:
        try:
            with av.open(str(path)) as container:
                audio_stream = None
                for stream in container.streams:
                    if stream.type == "audio":
                        audio_stream = stream
                        break
                if audio_stream is None:
                    raise MediaCommandError("no_audio_stream")

                tags = {}
                tags.update(container.metadata or {})
                tags.update(audio_stream.metadata or {})

                duration_sec = 0.0
                if audio_stream.duration is not None and audio_stream.time_base is not None:
                    duration_sec = float(audio_stream.duration * audio_stream.time_base)
                elif container.duration is not None:
                    duration_sec = float(container.duration / av.time_base)

                cover_width = None
                cover_height = None
                cover_bytes = None
                for stream in container.streams:
                    if stream.type != "video":
                        continue
                    try:
                        attached = bool(getattr(stream.disposition, "attached_pic", False))
                    except Exception:
                        attached = False
                    if not attached:
                        continue
                    cover_width = stream.codec_context.width or None
                    cover_height = stream.codec_context.height or None
                    try:
                        for packet in container.demux(stream):
                            frames = packet.decode()
                            if not frames:
                                continue
                            frame = frames[0]
                            array = frame.to_ndarray(format="rgb24")
                            cover_bytes = int(array.nbytes)
                            break
                    except Exception:
                        cover_bytes = None
                    break

                return ProbeInfo(
                    source_path=path,
                    codec=audio_stream.codec_context.name,
                    duration_sec=duration_sec,
                    sample_rate=audio_stream.codec_context.sample_rate,
                    channels=audio_stream.codec_context.channels,
                    bit_rate=audio_stream.bit_rate or container.bit_rate,
                    title=tags.get("title"),
                    artist=tags.get("artist"),
                    album=tags.get("album"),
                    format_name=container.format.name if container.format else None,
                    cover_width=cover_width,
                    cover_height=cover_height,
                    cover_bytes=cover_bytes,
                )
        except MediaCommandError:
            raise
        except Exception as exc:  # pragma: no cover - backend specific
            raise MediaCommandError(f"probe_failed:{path}:{exc}") from exc

