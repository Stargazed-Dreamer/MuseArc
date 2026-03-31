from __future__ import annotations

import json
import hashlib
import html
import difflib
import re
import shutil
import time
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from musearc.config.models import RuntimeConfig
from musearc.core.enums import DuplicateDecision, FileHealth, ReviewKind
from musearc.core.hashing import sha1_text, sha256_file
from musearc.core.ids import new_id
from musearc.core.models import Fingerprint, ImportProgress, ImportReport, LyricsInsert, ProbeInfo, ReviewItem, TrackInsert
from musearc.core.paths import ensure_parent, shard_relpath
from musearc.core.text_normalize import lrc_visible_lines, normalize_text
from musearc.infra.db.repositories import LibraryRepository
from musearc.infra.llm.client import LmStudioMatcher
from musearc.infra.media.audio_io import decode_audio
from musearc.infra.media.commands import MediaCommandError
from musearc.infra.media.fingerprint import AcousticFingerprintEngine
from musearc.infra.media.prober import MediaProbe
from musearc.infra.media.transcoder import MediaTranscoder
from musearc.services.dedupe import DuplicateEvaluator, infer_track_kind
from musearc.services.import_runtime import ImportControl, ResumeState, delete_resume_state, load_resume_state, resume_state_path
from musearc.services.lyrics_match import LyricsMatcher, read_text_guess_encoding
from musearc.services.scanner import scan_import_source


@dataclass(slots=True)
class ImportDependencies:
    probe: MediaProbe
    transcoder: MediaTranscoder
    fingerprint: AcousticFingerprintEngine


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _derive_title_artist(path: Path, probe_title: str | None, probe_artist: str | None) -> tuple[str, str]:
    if probe_title and probe_artist:
        return probe_title.strip(), probe_artist.strip()

    stem = path.stem
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        artist = artist.strip() or "Unknown Artist"
        title = title.strip() or stem.strip()
        return title, artist

    return (probe_title or stem.strip() or "Unknown Title"), (probe_artist or "Unknown Artist")


def _quality_score(duration_sec: float, bit_rate: int | None, source_ext: str) -> float:
    score = 0.35
    if bit_rate:
        score += min(0.4, bit_rate / 1_000_000)
    if duration_sec >= 180:
        score += 0.15
    ext = source_ext.lower().strip()
    format_bonus = {
        ".flac": 0.22,
        ".wav": 0.20,
        ".ape": 0.19,
        ".m4a": 0.13,
        ".aac": 0.12,
        ".opus": 0.12,
        ".ogg": 0.10,
        ".wma": 0.08,
        ".mp3": 0.06,
    }
    score += format_bonus.get(ext, 0.05)
    return min(1.0, max(0.0, score))


def _copy_file_and_sha256(source: Path, target: Path, chunk_size: int = 1024 * 1024) -> str:
    ensure_parent(target)
    digest = hashlib.sha256()
    with source.open("rb") as src, target.open("wb") as dst:
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            dst.write(chunk)
    try:
        shutil.copystat(source, target)
    except Exception:
        pass
    return digest.hexdigest()


def _as_json_dict(value: object) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return dict(parsed)
        except Exception:
            return {}
    return {}


def _normalize_track_ext_payload(payload: object) -> dict:
    data = _as_json_dict(payload)
    tags_raw = data.get("tags", {})
    if not isinstance(tags_raw, dict):
        tags_raw = {}
    tags: dict[str, str] = {}
    for key, value in tags_raw.items():
        k = str(key).strip()
        if not k:
            continue
        tags[k] = str(value or "")
    data["tags"] = tags
    return data


def _cover_payload_from_probe(probe: ProbeInfo) -> dict:
    width = _safe_int(probe.cover_width, 0)
    height = _safe_int(probe.cover_height, 0)
    byte_size = _safe_int(probe.cover_bytes, 0)
    if width <= 0 and height <= 0 and byte_size <= 0:
        return {}
    payload: dict[str, int] = {}
    if width > 0:
        payload["width"] = int(width)
    if height > 0:
        payload["height"] = int(height)
    if byte_size > 0:
        payload["bytes"] = int(byte_size)
    return payload


def _build_track_ext_payload(probe: ProbeInfo) -> dict:
    payload = {"tags": {}}
    cover = _cover_payload_from_probe(probe)
    if cover:
        payload["cover"] = cover
    return payload


def _cover_rank(value: object) -> tuple[int, int, int, int]:
    cover = value if isinstance(value, dict) else {}
    width = max(0, _safe_int(cover.get("width", 0), 0))
    height = max(0, _safe_int(cover.get("height", 0), 0))
    byte_size = max(0, _safe_int(cover.get("bytes", 0), 0))
    area = width * height
    edge = min(width, height) if width > 0 and height > 0 else 0
    has_cover = 1 if area > 0 or byte_size > 0 else 0
    return has_cover, area, edge, byte_size


def _merge_ext_payload_for_duplicate(primary_payload: object, secondary_payload: object) -> dict:
    primary = _normalize_track_ext_payload(primary_payload)
    secondary = _normalize_track_ext_payload(secondary_payload)
    merged = dict(secondary)
    merged.update(primary)

    secondary_tags = secondary.get("tags", {})
    primary_tags = primary.get("tags", {})
    merged_tags: dict[str, str] = {}
    if isinstance(secondary_tags, dict):
        merged_tags.update({str(k): str(v or "") for k, v in secondary_tags.items() if str(k).strip()})
    if isinstance(primary_tags, dict):
        merged_tags.update({str(k): str(v or "") for k, v in primary_tags.items() if str(k).strip()})
    merged["tags"] = merged_tags

    primary_cover = primary.get("cover")
    secondary_cover = secondary.get("cover")
    if _cover_rank(secondary_cover) > _cover_rank(primary_cover):
        best_cover = secondary_cover
        from_secondary = True
    else:
        best_cover = primary_cover
        from_secondary = False
    if isinstance(best_cover, dict) and best_cover:
        merged["cover"] = dict(best_cover)
        merged["cover_selected_from"] = "secondary" if from_secondary else "primary"
    else:
        merged.pop("cover", None)
        merged.pop("cover_selected_from", None)
    return merged


def _extract_lyrics_meta(text: str) -> tuple[str, int, str, str, str]:
    author = ""
    title = ""
    artist = ""
    album = ""

    for line in text.splitlines()[:40]:
        s = line.strip()
        if not s:
            continue
        low = s.casefold()

        def _tag_value(prefix: str) -> str:
            return s[len(prefix) : -1].strip()

        if low.startswith("[by:") and s.endswith("]") and not author:
            author = html.unescape(_tag_value("[by:"))
            continue
        if low.startswith("[ti:") and s.endswith("]") and not title:
            title = html.unescape(_tag_value("[ti:"))
            continue
        if low.startswith("[ar:") and s.endswith("]") and not artist:
            artist = html.unescape(_tag_value("[ar:"))
            continue
        if low.startswith("[al:") and s.endswith("]") and not album:
            album = html.unescape(_tag_value("[al:"))
            continue
        if low.startswith("by:") and not author:
            author = html.unescape(s[3:].strip())

    line_count = len([line for line in text.splitlines() if line.strip()])
    return author, line_count, title, artist, album


def _is_placeholder_empty_lyrics(text: str) -> bool:
    if not text:
        return True
    compact = "".join(ch for ch in text.strip() if not ch.isspace())
    compact = compact.replace("\ufeff", "")
    marker = "[00:00:00]此歌曲为没有填词的纯音乐，请您欣赏"
    marker_compact = "".join(ch for ch in marker if not ch.isspace())
    return compact == marker_compact


def _normalize_name_for_compare(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"[\(\[【{（].*?[\)\]】}）]", " ", text)
    return normalize_text(text)


def _lyrics_group_display_name(relpath: str) -> str:
    stem = Path(str(relpath or "")).stem.strip()
    if not stem:
        return "未分组"
    cleaned = re.sub(r"\s*[\(\[（【].*?[\)\]）】]\s*$", "", stem).strip()
    return cleaned or stem


def _name_similarity(a: str, b: str) -> float:
    na = _normalize_name_for_compare(a)
    nb = _normalize_name_for_compare(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    tokens_a = {t for t in na.split() if t}
    tokens_b = {t for t in nb.split() if t}
    if not tokens_a or not tokens_b:
        return 0.0
    inter = len(tokens_a.intersection(tokens_b))
    union = len(tokens_a.union(tokens_b))
    return 0.0 if union <= 0 else float(inter) / float(union)


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


class ImportService:
    def __init__(self, library_root: Path, runtime_cfg: RuntimeConfig):
        self.library_root = library_root
        self.runtime_cfg = runtime_cfg
        self.dependencies = ImportDependencies(
            probe=MediaProbe(),
            transcoder=MediaTranscoder(),
            fingerprint=AcousticFingerprintEngine(),
        )
        self.duplicate_evaluator = DuplicateEvaluator(self.dependencies.fingerprint, runtime_cfg.thresholds)
        llm = LmStudioMatcher(runtime_cfg.lmstudio) if runtime_cfg.lmstudio.enabled else None
        self.lyrics_matcher = LyricsMatcher(runtime_cfg.thresholds, llm)

    def _suggest_similar_tracks_by_name(self, source_stem: str, candidates: list[dict], limit: int = 6) -> list[dict]:
        scored: list[tuple[float, dict]] = []
        for row in candidates:
            candidate_name = str(row.get("title") or row.get("file_name") or "")
            score = _name_similarity(source_stem, candidate_name)
            if score <= 0.0:
                continue
            scored.append(
                (
                    score,
                    {
                        "track_id": str(row.get("track_id", "")),
                        "title": str(row.get("title", "")),
                        "artist": str(row.get("artist", "")),
                        "score": round(score, 4),
                    },
                )
            )
        scored.sort(key=lambda p: p[0], reverse=True)
        return [item for _score, item in scored[:limit]]

    def _fingerprint_with_loudness_normalization(
        self,
        source_path: Path,
        target_lufs: float = -14.0,
    ) -> Fingerprint:
        try:
            decoded = decode_audio(
                source_path,
                target_rate=22050,
                target_layout="mono",
                apply_loudnorm=True,
                target_lufs=target_lufs,
            )
        except MediaCommandError:
            raise
        except Exception as exc:
            raise MediaCommandError(f"decode_failed:{source_path}:{exc}") from exc

        samples = decoded.samples
        if samples.size <= 0:
            vector: list[int] = []
        else:
            vector = self.dependencies.fingerprint._fingerprint_vector(samples, decoded.sample_rate)
        if not vector:
            raise MediaCommandError(f"chromaprint_unavailable_or_failed:{source_path}")

        payload = self.dependencies.fingerprint.encode_vector(vector)
        digest = hashlib.sha1(payload.encode("ascii")).hexdigest()
        return Fingerprint(version=self.dependencies.fingerprint.version, vector=vector, digest=digest)

    def import_track_for_duplicate_review(
        self,
        repo: LibraryRepository,
        source_path: Path,
        *,
        existing_track_id: str | None = None,
        replace_existing: bool = True,
    ) -> dict:
        source = Path(source_path).expanduser().resolve()
        probe = self.dependencies.probe.probe(source)
        fp = self._fingerprint_with_loudness_normalization(source, target_lufs=-14.0)
        title, artist = _derive_title_artist(source, probe.title, probe.artist)
        quality = _quality_score(probe.duration_sec, probe.bit_rate, source.suffix)
        fp_payload = self.dependencies.fingerprint.encode_vector(fp.vector)
        ext_payload = _build_track_ext_payload(probe)
        source_sha = sha256_file(source)

        existing_by_sha = repo.get_track_by_source_sha(source_sha)
        if existing_by_sha and not existing_by_sha.get("deleted_at"):
            return {"status": "already_exists", "track_id": str(existing_by_sha.get("track_id", "") or "")}

        track_id = new_id("trk")
        ext_no_dot = source.suffix.lower().strip(".") or "bin"
        storage_rel = shard_relpath("data/tracks", track_id, ext_no_dot)
        storage_abs = self.library_root / Path(storage_rel)
        ensure_parent(storage_abs)
        shutil.copy2(source, storage_abs)

        track_row = TrackInsert(
            track_id=track_id,
            file_name=source.name,
            title=title,
            artist=artist,
            album=(probe.album or ""),
            language_kind="unknown",
            preference_level=5,
            storage_format=ext_no_dot,
            kind=infer_track_kind(title),
            duration_sec=probe.duration_sec,
            sample_rate=probe.sample_rate,
            channels=probe.channels,
            bit_rate=probe.bit_rate,
            quality_score=quality,
            storage_relpath=storage_rel,
            source_relpath=source.name,
            source_fullpath=str(source),
            source_sha256=source_sha,
            source_ext=source.suffix,
            probe_codec=probe.codec,
            file_health=FileHealth.OK,
            fingerprint_version=fp.version,
            fingerprint_digest=fp.digest,
            fingerprint_payload=fp_payload,
            imported_at=_utc_now(),
            ext_json=ext_payload,
        )
        try:
            repo.insert_track(track_row)
        except Exception:
            storage_abs.unlink(missing_ok=True)
            raise

        replaced_track_id = ""
        if existing_track_id:
            existing_rows = repo.get_tracks_by_ids([existing_track_id])
            existing = existing_rows[0] if existing_rows else {}
            if existing:
                merge_patch: dict[str, object] = {}
                if str(title or "").strip().casefold() in {"", "unknown", "unknown title"} and str(existing.get("title", "")).strip():
                    merge_patch["title"] = str(existing.get("title", "")).strip()
                if str(artist or "").strip().casefold() in {"", "unknown", "unknown artist"} and str(existing.get("artist", "")).strip():
                    merge_patch["artist"] = str(existing.get("artist", "")).strip()
                if not str(probe.album or "").strip() and str(existing.get("album", "")).strip():
                    merge_patch["album"] = str(existing.get("album", "")).strip()
                if merge_patch:
                    repo.update_tracks_fields([track_id], merge_patch)

                existing_ext = _normalize_track_ext_payload(existing.get("ext_json"))
                current_new_ext = _normalize_track_ext_payload(track_row.ext_json)
                merged_new_ext = _merge_ext_payload_for_duplicate(current_new_ext, existing_ext)
                if merged_new_ext != current_new_ext:
                    repo.update_track_ext_json(track_id, merged_new_ext)

            if replace_existing:
                old_relpaths = [
                    str(r.get("storage_relpath", "") or "")
                    for r in repo.get_tracks_by_ids([existing_track_id])
                    if str(r.get("storage_relpath", "") or "").strip()
                ]
                deleted = repo.soft_delete_tracks([existing_track_id])
                if deleted > 0:
                    replaced_track_id = str(existing_track_id)
                    for rel in old_relpaths:
                        try:
                            (self.library_root / rel).unlink(missing_ok=True)
                        except Exception:
                            pass

        return {
            "status": "imported",
            "track_id": track_id,
            "replaced_track_id": replaced_track_id,
        }

    def import_path(
        self,
        repo: LibraryRepository,
        source_path: Path,
        *,
        progress_callback: Callable[[ImportProgress], None] | None = None,
        control: ImportControl | None = None,
        resume: bool = True,
    ) -> ImportReport:
        source_path = source_path.resolve()
        state_file = resume_state_path(self.library_root, source_path)
        state = load_resume_state(state_file) if resume else None
        resumed = state is not None

        audio_files, lyrics_files = scan_import_source(source_path)
        scanned_files = len(audio_files) + len(lyrics_files)
        all_relpaths = [str(c.path.relative_to(source_path)).replace("\\", "/") for c in [*audio_files, *lyrics_files]]

        if state is None:
            start_time = _utc_now()
            state = ResumeState(
                version=1,
                import_batch_id=new_id("imp"),
                source_path=str(source_path),
                started_at=start_time.isoformat(),
                scanned_files=scanned_files,
                processed_files=0,
            )
            repo.start_import_batch(state.import_batch_id, str(source_path), start_time)
            self._save_state(state_file, repo, state)
        else:
            start_time = datetime.fromisoformat(state.started_at)
            state.scanned_files = scanned_files
            repo.start_import_batch(state.import_batch_id, str(source_path), start_time)

        file_state_map: dict[str, dict] = {}
        for rel in all_relpaths:
            file_state_map[rel] = {
                "relpath": rel,
                "file_name": Path(rel).name,
                "status": "待处理",
                "status_code": "pending",
                "reason": "",
            }
        for row in state.file_states or []:
            if not isinstance(row, dict):
                continue
            rel = str(row.get("relpath", "")).replace("\\", "/")
            if rel and rel in file_state_map:
                merged = dict(file_state_map[rel])
                merged.update({k: v for k, v in row.items() if k in {"status", "status_code", "reason"}})
                file_state_map[rel] = merged

        processed_relpaths = set(state.processed_relpaths)

        def snapshot_file_states() -> list[dict]:
            rows: list[dict] = []
            for rel in all_relpaths:
                state_row = file_state_map.get(rel)
                if not state_row:
                    continue
                rows.append(dict(state_row))
            return rows

        def set_processing(relpath: str, step: str) -> None:
            row = file_state_map.get(relpath)
            if not row:
                return
            row["status_code"] = "processing"
            row["status"] = f"处理中-{step}"
            row["reason"] = step

        def set_archived(relpath: str) -> None:
            row = file_state_map.get(relpath)
            if not row:
                return
            row["status_code"] = "archived"
            row["status"] = "已归档"
            row["reason"] = ""

        def set_review(relpath: str, reason: str) -> None:
            row = file_state_map.get(relpath)
            if not row:
                return
            text = str(reason or "").strip() or "待人工确认"
            row["status_code"] = "review"
            row["status"] = f"待审查-{text}"
            row["reason"] = text

        def set_skipped(relpath: str, reason: str) -> None:
            row = file_state_map.get(relpath)
            if not row:
                return
            text = str(reason or "").strip() or "已跳过"
            row["status_code"] = "skipped"
            row["status"] = f"已跳过-{text}"
            row["reason"] = text

        batch_track_records: list[dict] = []
        if state.created_track_ids:
            prior = repo.get_tracks_by_ids(state.created_track_ids)
            for item in prior:
                batch_track_records.append(
                    {
                        "track_id": item.get("track_id"),
                        "title": item.get("title", ""),
                        "artist": item.get("artist", ""),
                        "album": item.get("album", ""),
                        "source_stem": Path(item.get("source_relpath") or "").stem,
                        "storage_relpath": item.get("storage_relpath", ""),
                    }
                )

        lyrics_review_groups: list[dict] = []

        def resolve_lyrics_group_key(relpath: str, lyrics_text: str) -> tuple[str, str]:
            stem_norm = _normalize_name_for_compare(Path(relpath).stem)
            lines = lrc_visible_lines(lyrics_text, max_lines=80)
            text_norm = " ".join(normalize_text(line) for line in lines)
            for group in lyrics_review_groups:
                name_sim = difflib.SequenceMatcher(None, stem_norm, group["stem_norm"]).ratio()
                text_sim = (
                    difflib.SequenceMatcher(None, text_norm, group["text_norm"]).ratio()
                    if text_norm and group["text_norm"]
                    else 0.0
                )
                if name_sim >= 0.92 or text_sim >= 0.90:
                    return str(group["group_key"]), str(group.get("group_title") or group["group_key"])

            group_key = _lyrics_group_display_name(relpath)
            lyrics_review_groups.append(
                {
                    "group_key": group_key,
                    "group_title": _lyrics_group_display_name(relpath),
                    "stem_norm": stem_norm,
                    "text_norm": text_norm,
                }
            )
            return group_key, _lyrics_group_display_name(relpath)

        last_emit_ts = 0.0

        def emit(stage: str, current_file: str = "", force: bool = False, paused: bool = False) -> None:
            nonlocal last_emit_ts
            if not progress_callback:
                return
            now = time.monotonic()
            if not force and now - last_emit_ts < 0.2:
                return
            last_emit_ts = now
            progress_callback(
                ImportProgress(
                    import_batch_id=state.import_batch_id,
                    source_path=str(source_path),
                    stage=stage,
                    current_file=current_file,
                    scanned_files=state.scanned_files,
                    processed_files=state.processed_files,
                    imported_tracks=state.imported_tracks,
                    duplicate_tracks=state.duplicate_tracks,
                    imported_lyrics=state.imported_lyrics,
                    matched_lyrics=state.matched_lyrics,
                    review_items=state.review_items,
                    errors=len(state.errors),
                    resumed=resumed,
                    paused=paused,
                    file_states=snapshot_file_states(),
                )
            )

        def mark_processed(relpath: str) -> None:
            if relpath in processed_relpaths:
                return
            processed_relpaths.add(relpath)
            state.processed_relpaths.append(relpath)
            state.processed_files = len(processed_relpaths)
            state.file_states = snapshot_file_states()
            self._save_state(state_file, repo, state)

        state.file_states = snapshot_file_states()
        emit("start", force=True)
        with nullcontext():
            for candidate in audio_files:
                relpath = str(candidate.path.relative_to(source_path)).replace("\\", "/")
                if relpath in processed_relpaths:
                    continue

                cancelled, mode = self._wait_control(control, emit, relpath)
                if cancelled:
                    state.file_states = snapshot_file_states()
                    return self._handle_cancel(repo, state, state_file, start_time, rollback=(mode == "rollback"), emit=emit)

                pending_review_reason: str | None = None

                set_processing(relpath, "音频探测")
                emit("audio_probe", relpath)
                try:
                    probe = self.dependencies.probe.probe(candidate.path)
                except MediaCommandError as exc:
                    state.errors.append(f"probe_failed:{candidate.path}:{exc}")
                    state.review_items += 1
                    self._enqueue_review(
                        repo,
                        ReviewItem(
                            kind=ReviewKind.FILE_ISSUE,
                            title="音频探测失败",
                            payload={"path": str(candidate.path), "error": str(exc)},
                            priority=3,
                        ),
                    )
                    set_review(relpath, "音频探测失败")
                    mark_processed(relpath)
                    continue

                if probe.duration_sec < self.runtime_cfg.thresholds.min_track_duration_sec:
                    state.review_items += 1
                    self._enqueue_review(
                        repo,
                        ReviewItem(
                            kind=ReviewKind.FILE_ISSUE,
                            title="疑似试听或哑文件",
                            payload={"path": str(candidate.path), "duration_sec": probe.duration_sec},
                            priority=2,
                        ),
                    )
                    set_review(relpath, "疑似试听或哑文件")
                    mark_processed(relpath)
                    continue

                set_processing(relpath, "指纹提取")
                emit("audio_loudnorm", relpath)
                emit("audio_fingerprint", relpath)
                try:
                    fp = self._fingerprint_with_loudness_normalization(
                        candidate.path,
                        target_lufs=-14.0,
                    )
                except MediaCommandError as exc:
                    err_text = str(exc).casefold()
                    issue_title = "指纹提取失败"
                    issue_reason = "指纹提取失败"
                    if "loudnorm" in err_text:
                        issue_title = "响度归一不可用"
                        issue_reason = "响度归一不可用"
                    state.errors.append(f"fingerprint_failed:{candidate.path}:{exc}")
                    state.review_items += 1
                    suggest_pool = repo.find_duplicate_candidates(probe.duration_sec, tolerance_sec=30.0)
                    suggestions = self._suggest_similar_tracks_by_name(candidate.path.stem, suggest_pool)
                    self._enqueue_review(
                        repo,
                        ReviewItem(
                            kind=ReviewKind.FILE_ISSUE,
                            title=issue_title,
                            payload={
                                "path": str(candidate.path),
                                "error": str(exc),
                                "title_hint": candidate.path.stem,
                                "suggest_candidates": suggestions,
                                "group_key": suggestions[0].get("track_id") if suggestions else "",
                            },
                            priority=3,
                        ),
                    )
                    set_review(relpath, issue_reason)
                    mark_processed(relpath)
                    continue

                title, artist = _derive_title_artist(candidate.path, probe.title, probe.artist)
                quality = _quality_score(probe.duration_sec, probe.bit_rate, candidate.ext)
                fp_payload = self.dependencies.fingerprint.encode_vector(fp.vector)
                new_ext_payload = _build_track_ext_payload(probe)
                set_processing(relpath, "源去重")

                dedupe_candidates = repo.find_duplicate_candidates(probe.duration_sec)
                decision = self.duplicate_evaluator.decide(
                    new_payload=fp_payload,
                    new_quality=quality,
                    new_title=title,
                    new_source_ext=candidate.ext,
                    candidates=dedupe_candidates,
                )

                if decision.decision == DuplicateDecision.KEEP_EXISTING:
                    if decision.existing_track_id:
                        existing_rows = repo.get_tracks_by_ids([decision.existing_track_id])
                        existing = existing_rows[0] if existing_rows else {}
                        if existing:
                            existing_ext = _normalize_track_ext_payload(existing.get("ext_json"))
                            merged_existing_ext = _merge_ext_payload_for_duplicate(existing_ext, new_ext_payload)
                            if merged_existing_ext != existing_ext:
                                repo.update_track_ext_json(str(existing.get("track_id", "") or decision.existing_track_id), merged_existing_ext)
                    state.duplicate_tracks += 1
                    set_skipped(relpath, "重复且保留已有")
                    mark_processed(relpath)
                    continue

                if decision.decision == DuplicateDecision.REVIEW:
                    state.review_items += 1
                    self._enqueue_review(
                        repo,
                        ReviewItem(
                            kind=ReviewKind.DUPLICATE,
                            title="疑似重复音频",
                            payload={
                                "path": str(candidate.path),
                                "score": decision.score,
                                "existing_track_id": decision.existing_track_id,
                                "reason": decision.reason,
                                "deferred_import": True,
                                "replace_existing_suggested": True,
                            },
                            priority=3,
                        ),
                    )
                    set_review(relpath, "疑似重复音频")
                    mark_processed(relpath)
                    continue

                track_id = new_id("trk")
                ext_no_dot = candidate.ext.lower().strip(".") or "bin"
                storage_rel = shard_relpath("data/tracks", track_id, ext_no_dot)
                storage_abs = self.library_root / Path(storage_rel)
                ensure_parent(storage_abs)

                set_processing(relpath, "归档")
                emit("audio_copy", relpath)
                try:
                    source_sha = _copy_file_and_sha256(candidate.path, storage_abs)
                except Exception as exc:
                    state.errors.append(f"copy_failed:{candidate.path}:{exc}")
                    state.review_items += 1
                    self._enqueue_review(
                        repo,
                        ReviewItem(
                            kind=ReviewKind.FILE_ISSUE,
                            title="复制归档失败",
                            payload={"path": str(candidate.path), "error": str(exc)},
                            priority=3,
                        ),
                    )
                    if storage_abs.exists():
                        storage_abs.unlink(missing_ok=True)
                    set_review(relpath, "复制归档失败")
                    mark_processed(relpath)
                    continue

                existing_by_sha = repo.get_track_by_source_sha(source_sha)
                if existing_by_sha:
                    if storage_abs.exists():
                        storage_abs.unlink(missing_ok=True)
                    if existing_by_sha.get("deleted_at"):
                        state.review_items += 1
                        self._enqueue_review(
                            repo,
                            ReviewItem(
                                kind=ReviewKind.DUPLICATE,
                                title="已删除歌曲重新导入",
                                payload={
                                    "path": str(candidate.path),
                                    "score": 1.0,
                                    "reason": "命中已删除曲目",
                                    "existing_track_id": str(existing_by_sha.get("track_id", "") or ""),
                                    "group_key": str(existing_by_sha.get("track_id", "") or "")[:8],
                                    "deferred_import": True,
                                },
                                priority=2,
                            ),
                        )
                        set_review(relpath, "命中已删除歌曲，待确认是否重新导入")
                    else:
                        state.duplicate_tracks += 1
                        set_skipped(relpath, "source_sha256重复")
                    mark_processed(relpath)
                    continue

                track_row = TrackInsert(
                    track_id=track_id,
                    file_name=candidate.path.name,
                    title=title,
                    artist=artist,
                    album=(probe.album or ""),
                    language_kind="unknown",
                    preference_level=5,
                    storage_format=ext_no_dot,
                    kind=infer_track_kind(title),
                    duration_sec=probe.duration_sec,
                    sample_rate=probe.sample_rate,
                    channels=probe.channels,
                    bit_rate=probe.bit_rate,
                    quality_score=quality,
                    storage_relpath=storage_rel,
                    source_relpath=relpath,
                    source_fullpath=str(candidate.path.resolve()),
                    source_sha256=source_sha,
                    source_ext=candidate.ext,
                    probe_codec=probe.codec,
                    file_health=FileHealth.OK,
                    fingerprint_version=fp.version,
                    fingerprint_digest=fp.digest,
                    fingerprint_payload=fp_payload,
                    imported_at=_utc_now(),
                    ext_json=new_ext_payload,
                )
                try:
                    repo.insert_track(track_row)
                except Exception as exc:
                    msg = str(exc).lower()
                    if "unique constraint failed: tracks.source_sha256" in msg:
                        state.duplicate_tracks += 1
                        if storage_abs.exists():
                            storage_abs.unlink(missing_ok=True)
                        set_skipped(relpath, "source_sha256重复")
                        mark_processed(relpath)
                        continue
                    state.errors.append(f"insert_failed:{candidate.path}:{exc}")
                    state.review_items += 1
                    self._enqueue_review(
                        repo,
                        ReviewItem(
                            kind=ReviewKind.FILE_ISSUE,
                            title="写入数据库失败",
                            payload={"path": str(candidate.path), "error": str(exc)},
                            priority=3,
                        ),
                    )
                    if storage_abs.exists():
                        storage_abs.unlink(missing_ok=True)
                    set_review(relpath, "写入数据库失败")
                    mark_processed(relpath)
                    continue

                if decision.existing_track_id:
                    existing_rows = repo.get_tracks_by_ids([decision.existing_track_id])
                    existing = existing_rows[0] if existing_rows else {}
                    if existing:
                        merge_patch: dict[str, object] = {}

                        def _needs_fill(field_key: str, current: str) -> bool:
                            text = str(current or "").strip().casefold()
                            if field_key == "title":
                                return text in {"", "unknown title", "unknown"}
                            if field_key == "artist":
                                return text in {"", "unknown artist", "unknown"}
                            if field_key == "language_kind":
                                return text in {"", "unknown"}
                            return text == ""

                        if _needs_fill("title", title) and str(existing.get("title", "")).strip():
                            merge_patch["title"] = str(existing.get("title", "")).strip()
                        if _needs_fill("artist", artist) and str(existing.get("artist", "")).strip():
                            merge_patch["artist"] = str(existing.get("artist", "")).strip()
                        if _needs_fill("album", probe.album or "") and str(existing.get("album", "")).strip():
                            merge_patch["album"] = str(existing.get("album", "")).strip()
                        if _needs_fill("language_kind", "unknown") and str(existing.get("language_kind", "")).strip():
                            merge_patch["language_kind"] = str(existing.get("language_kind", "")).strip()
                        if merge_patch:
                            repo.update_tracks_fields([track_id], merge_patch)
                            title = str(merge_patch.get("title", title))
                            artist = str(merge_patch.get("artist", artist))

                        existing_ext = _normalize_track_ext_payload(existing.get("ext_json"))
                        current_new_ext = _normalize_track_ext_payload(track_row.ext_json)
                        merged_new_ext = _merge_ext_payload_for_duplicate(current_new_ext, existing_ext)
                        if merged_new_ext != current_new_ext:
                            repo.update_track_ext_json(track_id, merged_new_ext)
                            track_row.ext_json = merged_new_ext

                state.imported_tracks += 1
                state.created_track_ids.append(track_id)
                state.created_storage_relpaths.append(storage_rel)

                batch_track_records.append(
                    {
                        "track_id": track_id,
                        "title": title,
                        "artist": artist,
                        "album": probe.album or "",
                        "source_stem": candidate.path.stem,
                        "storage_relpath": storage_rel,
                    }
                )

                if decision.existing_track_id and decision.score >= self.runtime_cfg.thresholds.duplicate_review:
                    repo.add_variant(
                        variant_id=new_id("var"),
                        primary_track_id=decision.existing_track_id,
                        variant_track_id=track_id,
                        relation_type="similar_version",
                        similarity_score=decision.score,
                        reason=decision.reason,
                    )

                if decision.decision == DuplicateDecision.KEEP_NEW and decision.existing_track_id:
                    old_relpaths = [
                        str(r.get("storage_relpath", "") or "")
                        for r in repo.get_tracks_by_ids([decision.existing_track_id])
                        if str(r.get("storage_relpath", "") or "").strip()
                    ]
                    deleted = repo.soft_delete_tracks([decision.existing_track_id])
                    if deleted > 0:
                        state.soft_deleted_existing_ids.append(decision.existing_track_id)
                        for rel in old_relpaths:
                            try:
                                (self.library_root / rel).unlink(missing_ok=True)
                            except Exception:
                                pass
                    else:
                        state.review_items += 1
                        pending_review_reason = pending_review_reason or "预期替换旧版本但未删除"
                        self._enqueue_review(
                            repo,
                            ReviewItem(
                                kind=ReviewKind.DUPLICATE,
                                title="预期替换旧版本但未删除",
                                payload={
                                    "new_track_id": track_id,
                                    "existing_track_id": decision.existing_track_id,
                                },
                                priority=2,
                            ),
                        )

                if pending_review_reason:
                    set_review(relpath, pending_review_reason)
                else:
                    set_archived(relpath)
                mark_processed(relpath)

        for candidate in lyrics_files:
            relpath = str(candidate.path.relative_to(source_path)).replace("\\", "/")
            if relpath in processed_relpaths:
                continue

            cancelled, mode = self._wait_control(control, emit, relpath)
            if cancelled:
                state.file_states = snapshot_file_states()
                return self._handle_cancel(repo, state, state_file, start_time, rollback=(mode == "rollback"), emit=emit)

            set_processing(relpath, "读取歌词")
            emit("lyrics_read", relpath)
            try:
                text, enc = read_text_guess_encoding(candidate.path)
                text = html.unescape(text)
            except Exception as exc:
                state.errors.append(f"lyrics_read_failed:{candidate.path}:{exc}")
                state.review_items += 1
                self._enqueue_review(
                    repo,
                    ReviewItem(
                        kind=ReviewKind.FILE_ISSUE,
                        title="歌词读取失败",
                        payload={"path": str(candidate.path), "error": str(exc)},
                        priority=2,
                    ),
                )
                set_review(relpath, "歌词读取失败")
                mark_processed(relpath)
                continue

            if _is_placeholder_empty_lyrics(text):
                try:
                    placeholder_match = self.lyrics_matcher.match_one(candidate.stem_normalized, text, batch_track_records)
                    if placeholder_match.track_id and float(placeholder_match.score or 0.0) >= 0.65:
                        repo.update_tracks_fields([str(placeholder_match.track_id)], {"language_kind": "instrumental"})
                except Exception:
                    pass
                set_skipped(relpath, "纯音乐占位歌词")
                mark_processed(relpath)
                continue

            text_hash = sha1_text(text)
            lyrics_author, line_count, lyrics_title, lyrics_artist, lyrics_album = _extract_lyrics_meta(text)
            existing_lyrics = repo.get_lyrics_by_text_hash(text_hash)
            if existing_lyrics and existing_lyrics.get("deleted_at"):
                state.review_items += 1
                lyrics_group_key, lyrics_group_title = resolve_lyrics_group_key(relpath, text)
                self._enqueue_review(
                    repo,
                    ReviewItem(
                        kind=ReviewKind.LYRICS_MATCH,
                        title="已删除歌词重新导入",
                        payload={
                            "lyrics_source": relpath,
                            "lyrics_id": str(existing_lyrics.get("lyrics_id", "") or ""),
                            "suggest_track_id": "",
                            "score": 1.0,
                            "reason": "命中已删除歌词",
                            "lyrics_preview": text.splitlines()[:10],
                            "group_key": lyrics_group_key,
                            "lyrics_group_key": lyrics_group_key,
                            "lyrics_group_title": lyrics_group_title,
                        },
                        priority=2,
                    ),
                )
                set_review(relpath, "命中已删除歌词，待确认是否重新导入")
                mark_processed(relpath)
                continue

            if existing_lyrics:
                lyrics_id = str(existing_lyrics.get("lyrics_id", "") or "")
            else:
                lyrics_id = new_id("lrc")
                lyrics_rel = shard_relpath("data/lyrics", lyrics_id, "lrc")
                lyrics_abs = self.library_root / Path(lyrics_rel)
                ensure_parent(lyrics_abs)
                try:
                    lyrics_abs.write_text(text, encoding="utf-8")
                    repo.insert_lyrics(
                        LyricsInsert(
                            lyrics_id=lyrics_id,
                            source_relpath=relpath,
                            storage_relpath=lyrics_rel,
                            text_hash=text_hash,
                            raw_encoding=enc,
                            lyrics_title=lyrics_title,
                            lyrics_artist=lyrics_artist,
                            lyrics_album=lyrics_album,
                            lyrics_author=lyrics_author,
                            line_count=line_count,
                            imported_at=_utc_now(),
                        )
                    )
                except Exception as exc:
                    state.errors.append(f"lyrics_insert_failed:{candidate.path}:{exc}")
                    state.review_items += 1
                    self._enqueue_review(
                        repo,
                        ReviewItem(
                            kind=ReviewKind.FILE_ISSUE,
                            title="歌词写入失败",
                            payload={"path": str(candidate.path), "error": str(exc)},
                            priority=2,
                        ),
                    )
                    if lyrics_abs.exists():
                        lyrics_abs.unlink(missing_ok=True)
                    set_review(relpath, "歌词写入失败")
                    mark_processed(relpath)
                    continue
                state.imported_lyrics += 1
                state.created_lyrics_ids.append(lyrics_id)
                state.created_storage_relpaths.append(lyrics_rel)

            set_processing(relpath, "歌词匹配")
            emit("lyrics_match", relpath)
            try:
                match = self.lyrics_matcher.match_one(candidate.stem_normalized, text, batch_track_records)
                if match.track_id and not match.needs_review:
                    repo.link_lyrics(
                        track_id=match.track_id,
                        lyrics_id=lyrics_id,
                        confidence=match.score,
                        match_method=match.reason,
                    )
                    state.matched_lyrics += 1
                    set_archived(relpath)
                else:
                    state.review_items += 1
                    lyrics_group_key, lyrics_group_title = resolve_lyrics_group_key(relpath, text)
                    lyrics_suggestions: list[dict] = []
                    for item in batch_track_records:
                        score = _name_similarity(candidate.path.stem, str(item.get("title") or item.get("source_stem") or ""))
                        if score <= 0.0:
                            continue
                        lyrics_suggestions.append(
                            {
                                "track_id": str(item.get("track_id", "")),
                                "title": str(item.get("title", "")),
                                "artist": str(item.get("artist", "")),
                                "score": round(score, 4),
                            }
                        )
                    lyrics_suggestions.sort(key=lambda r: float(r.get("score", 0.0)), reverse=True)
                    lyrics_suggestions = lyrics_suggestions[:6]
                    self._enqueue_review(
                        repo,
                        ReviewItem(
                            kind=ReviewKind.LYRICS_MATCH,
                            title="歌词匹配待人工审查",
                            payload={
                                "lyrics_source": relpath,
                                "lyrics_id": lyrics_id,
                                "suggest_track_id": match.track_id,
                                "score": match.score,
                                "reason": match.reason,
                                "discard_reason": "匹配置信度不足",
                                "lyrics_preview": text.splitlines()[:10],
                                "title_hint": candidate.path.stem,
                                "suggest_candidates": lyrics_suggestions,
                                "group_key": lyrics_suggestions[0].get("track_id") if lyrics_suggestions else lyrics_group_key,
                                "lyrics_group_key": lyrics_group_key,
                                "lyrics_group_title": lyrics_group_title,
                            },
                            priority=2,
                        ),
                    )
                    if match.track_id:
                        repo.link_lyrics(
                            track_id=match.track_id,
                            lyrics_id=lyrics_id,
                            confidence=match.score,
                            match_method=f"review:{match.reason}",
                            is_primary=False,
                        )
                    set_review(relpath, "歌词匹配待人工审查")
            except Exception as exc:
                state.errors.append(f"lyrics_match_failed:{candidate.path}:{exc}")
                state.review_items += 1
                self._enqueue_review(
                    repo,
                    ReviewItem(
                        kind=ReviewKind.FILE_ISSUE,
                        title="歌词匹配执行失败",
                        payload={"path": str(candidate.path), "error": str(exc)},
                        priority=2,
                    ),
                )
                set_review(relpath, "歌词匹配执行失败")

            mark_processed(relpath)

        end_time = _utc_now()
        state.file_states = snapshot_file_states()
        repo.finish_import_batch(
            state.import_batch_id,
            scanned_files=state.scanned_files,
            imported_tracks=state.imported_tracks,
            duplicate_tracks=state.duplicate_tracks,
            imported_lyrics=state.imported_lyrics,
            matched_lyrics=state.matched_lyrics,
            review_items=state.review_items,
            errors=state.errors,
            finished_at=end_time,
        )
        delete_resume_state(state_file)

        report = ImportReport(
            import_batch_id=state.import_batch_id,
            source_path=str(source_path),
            started_at=start_time,
            finished_at=end_time,
            scanned_files=state.scanned_files,
            imported_tracks=state.imported_tracks,
            duplicate_tracks=state.duplicate_tracks,
            imported_lyrics=state.imported_lyrics,
            matched_lyrics=state.matched_lyrics,
            review_items=state.review_items,
            errors=state.errors,
            cancelled=False,
            rollback_applied=False,
            resume_available=False,
            file_states=state.file_states,
        )

        self._write_manifest(report)
        emit("done", force=True)
        return report

    def _save_state(self, state_file: Path, repo: LibraryRepository, state: ResumeState) -> None:
        from musearc.services.import_runtime import save_resume_state

        save_resume_state(state_file, state)
        repo.update_import_batch_progress(
            state.import_batch_id,
            scanned_files=state.scanned_files,
            imported_tracks=state.imported_tracks,
            duplicate_tracks=state.duplicate_tracks,
            imported_lyrics=state.imported_lyrics,
            matched_lyrics=state.matched_lyrics,
            review_items=state.review_items,
            errors=state.errors,
        )

    def _wait_control(self, control: ImportControl | None, emit, current_file: str) -> tuple[bool, str]:
        if control is None:
            return False, "keep"
        while control.is_paused():
            emit("paused", current_file, force=True, paused=True)
            control.wait_if_paused(timeout_sec=0.2)
            cancelled, mode, _ = control.snapshot()
            if cancelled:
                return True, mode
        cancelled, mode, _ = control.snapshot()
        return cancelled, mode

    def _handle_cancel(
        self,
        repo: LibraryRepository,
        state: ResumeState,
        state_file: Path,
        start_time: datetime,
        *,
        rollback: bool,
        emit,
    ) -> ImportReport:
        end_time = _utc_now()
        rollback_applied = False
        resume_available = True
        state.file_states = list(state.file_states or [])

        if rollback:
            self._rollback_partial(repo, state)
            repo.delete_import_batch(state.import_batch_id)
            delete_resume_state(state_file)
            rollback_applied = True
            resume_available = False
        else:
            self._save_state(state_file, repo, state)

        report = ImportReport(
            import_batch_id=state.import_batch_id,
            source_path=state.source_path,
            started_at=start_time,
            finished_at=end_time,
            scanned_files=state.scanned_files,
            imported_tracks=state.imported_tracks,
            duplicate_tracks=state.duplicate_tracks,
            imported_lyrics=state.imported_lyrics,
            matched_lyrics=state.matched_lyrics,
            review_items=state.review_items,
            errors=state.errors,
            cancelled=True,
            rollback_applied=rollback_applied,
            resume_available=resume_available,
            file_states=list(state.file_states or []),
        )
        self._write_manifest(report)
        emit("cancelled", force=True)
        return report

    def _rollback_partial(self, repo: LibraryRepository, state: ResumeState) -> None:
        repo.hard_delete_tracks(state.created_track_ids)
        repo.delete_lyrics_by_ids(state.created_lyrics_ids)
        repo.restore_tracks(state.soft_deleted_existing_ids)
        for rel in set(state.created_storage_relpaths):
            target = self.library_root / rel
            if target.exists() and target.is_file():
                target.unlink(missing_ok=True)

    def _enqueue_review(self, repo: LibraryRepository, item: ReviewItem) -> None:
        repo.enqueue_review(new_id("rev"), item)

    def _write_manifest(self, report: ImportReport) -> None:
        manifests = self.library_root / "manifests" / "imports"
        manifests.mkdir(parents=True, exist_ok=True)
        target = manifests / f"{report.import_batch_id}.json"
        target.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
