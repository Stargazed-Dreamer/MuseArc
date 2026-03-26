from __future__ import annotations

import re
from pathlib import Path

from musearc.infra.db.repositories import LibraryRepository
from musearc.infra.media.transcoder import ExportFormat, MediaTranscoder


def _safe_name(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]", "_", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or "unknown"


class ExportService:
    def __init__(self, library_root: Path):
        self.library_root = library_root
        self.transcoder = MediaTranscoder()

    def export_tracks(
        self,
        repo: LibraryRepository,
        track_ids: list[str],
        out_dir: Path,
        *,
        fmt: str,
        bitrate: str | None,
        sample_rate: int | None,
    ) -> list[Path]:
        format_plan = {track_id: fmt for track_id in track_ids}
        return self.export_tracks_with_plan(
            repo,
            track_ids,
            out_dir,
            format_plan=format_plan,
            bitrate=bitrate,
            sample_rate=sample_rate,
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
    ) -> list[Path]:
        records = repo.get_tracks_by_ids(track_ids)
        out_dir.mkdir(parents=True, exist_ok=True)
        exported: list[Path] = []

        for record in records:
            source = self.library_root / record["storage_relpath"]
            track_id = str(record.get("track_id", ""))
            chosen_fmt = str(format_plan.get(track_id, "original") or "original").lower().strip(".")
            source_fmt = str(record.get("storage_format") or record.get("source_ext") or "").lower().strip(".")
            if chosen_fmt in {"", "original"}:
                chosen_fmt = source_fmt or "bin"

            file_name = _safe_name(f"{record['artist']} - {record['title']}")
            target = out_dir / f"{file_name}.{chosen_fmt}"
            if chosen_fmt == source_fmt:
                target.write_bytes(source.read_bytes())
            else:
                options = ExportFormat(fmt=chosen_fmt, bitrate=bitrate, sample_rate=sample_rate)
                self.transcoder.export_audio(source, target, options)
            exported.append(target)

        return exported
