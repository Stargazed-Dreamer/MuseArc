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
                )
        except MediaCommandError:
            raise
        except Exception as exc:  # pragma: no cover - backend specific
            raise MediaCommandError(f"probe_failed:{path}:{exc}") from exc
