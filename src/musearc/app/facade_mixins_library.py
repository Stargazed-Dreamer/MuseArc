from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


from musearc.core.hashing import sha1_text
from musearc.services.importer import ImportService
from musearc.services.library_ops import LibraryOpsService

FAVORITES_PLAYLIST_ID = "pl_favorites"

_TIMESTAMP_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merge_lyrics_text(primary_text: str, secondary_text: str) -> str:
    def _collect(text: str) -> tuple[list[tuple[int, int, str, str]], list[str]]:
        timed: list[tuple[int, int, str, str]] = []
        untimed: list[str] = []
        order = 0
        for raw in str(text or "").splitlines():
            line = str(raw or "").rstrip("\r\n")
            if not line.strip():
                continue
            stamps = list(_TIMESTAMP_RE.finditer(line))
            if not stamps:
                untimed.append(line.strip())
                continue
            content = _TIMESTAMP_RE.sub("", line).strip()
            for match in stamps:
                mm = int(match.group(1))
                ss = int(match.group(2))
                frac_raw = str(match.group(3) or "0")
                frac = int((frac_raw + "00")[:2])
                centisec = mm * 6000 + ss * 100 + frac
                tag = f"[{mm:02d}:{ss:02d}.{frac:02d}]"
                timed.append((centisec, order, tag, content))
                order += 1
        return timed, untimed

    timed_primary, untimed_primary = _collect(primary_text)
    timed_secondary, untimed_secondary = _collect(secondary_text)

    merged_timed: dict[int, tuple[int, str, str]] = {}
    for centisec, order, tag, content in timed_primary:
        merged_timed[centisec] = (order, tag, content)
    for centisec, order, tag, content in timed_secondary:
        existing = merged_timed.get(centisec)
        if existing is None:
            merged_timed[centisec] = (10_000 + order, tag, content)
            continue
        old_order, old_tag, old_content = existing
        if content and content not in old_content.split(" | "):
            joined = f"{old_content} | {content}" if old_content else content
            merged_timed[centisec] = (old_order, old_tag, joined)

    merged_lines: list[str] = []
    for centisec, payload in sorted(merged_timed.items(), key=lambda item: (item[0], item[1][0])):
        _order, tag, content = payload
        merged_lines.append(f"{tag}{content}".rstrip())

    seen_untimed: set[str] = set()
    for line in [*untimed_primary, *untimed_secondary]:
        text = str(line or "").strip()
        if not text or text in seen_untimed:
            continue
        seen_untimed.add(text)
        merged_lines.append(text)
    return "\n".join(merged_lines).strip()

class FacadeLibraryMixin:
    """Facade mixin: library/review/playlist/tag/lyrics workflows."""

    def pending_reviews(self, limit: int = 100) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1apending_reviews\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).pending_reviews(limit)

    def resolve_reviews(self, review_ids: list[str], status: str = "resolved") -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aresolve_reviews\u3002"""
        ids = [rid for rid in review_ids if rid]
        if not ids:
            return 0
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            pending_map = {str(r.get("review_id", "")): r for r in repo.list_pending_reviews(limit=200_000)}
            target_ids = [rid for rid in ids if rid in pending_map]
            if not target_ids:
                return 0
            count = LibraryOpsService(repo).resolve_reviews(target_ids, status=status)
            if count > 0:
                self._append_undo(
                    repo,
                    "resolve_reviews",
                    {
                        "review_ids": target_ids,
                        "status_after": "ignored" if status == "ignored" else "resolved",
                    },
                )
        if count > 0:
            self._redo_actions.clear()
            self._log(f"resolve_reviews count={count} status={status}")
        return count

    def import_track_from_review(
        self,
        source_path: str,
        *,
        existing_track_id: str | None = None,
        replace_existing: bool = True,
    ) -> dict:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aimport_track_from_review\u3002"""
        source = Path(str(source_path or "")).expanduser().resolve()
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            result = ImportService(self.ctx.layout.root, self.ctx.runtime_config).import_track_for_duplicate_review(
                repo,
                source,
                existing_track_id=existing_track_id,
                replace_existing=replace_existing,
            )
        if str(result.get("status", "")) == "imported":
            self._redo_actions.clear()
            self._log(
                f"import_track_from_review source={source} track={result.get('track_id','')} replaced={result.get('replaced_track_id','')}"
            )
        return result

    def delete_tracks(self, track_ids: list[str], *, mode: str = "move_linked_lyrics") -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1adelete_tracks\u3002"""
        delete_mode = mode if mode in {"move_linked_lyrics", "unlink_only"} else "move_linked_lyrics"
        count = 0
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            affected = repo.get_tracks_by_ids(track_ids)
            count = LibraryOpsService(repo).delete_tracks(track_ids, mode=delete_mode)
            if count > 0:
                self._append_undo(
                    repo,
                    "soft_delete_tracks",
                    {"track_ids": [r["track_id"] for r in affected], "mode": delete_mode},
                )
                self._log(f"delete_tracks count={count} mode={delete_mode}")
        return count

    def list_deleted_tracks(self, limit: int = 5000, *, include_missing: bool = True) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_deleted_tracks\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            rows = LibraryOpsService(LibraryRepository(conn)).list_deleted_tracks(limit)
        if include_missing:
            return rows
        visible: list[dict] = []
        for row in rows:
            rel = str(row.get("storage_relpath", "") or "").strip()
            if not rel:
                visible.append(row)
                continue
            if (self.ctx.layout.root / rel).exists():
                visible.append(row)
        return visible

    def list_deleted_items(self, limit: int = 5000) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_deleted_items\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            tracks = repo.list_deleted_tracks(limit=limit)
            lyrics = repo.list_deleted_lyrics(limit=limit)

        out: list[dict] = []
        for row in tracks:
            rel = str(row.get("storage_relpath", "") or "").strip()
            out.append(
                {
                    "item_type": "track",
                    "item_type_label": "歌曲",
                    "item_id": str(row.get("track_id", "") or ""),
                    "file_name": str(row.get("file_name", "") or ""),
                    "title": str(row.get("title", "") or ""),
                    "artist": str(row.get("artist", "") or ""),
                    "album": str(row.get("album", "") or ""),
                    "storage_relpath": rel,
                    "deleted_at": str(row.get("deleted_at", "") or ""),
                    "file_exists": bool(rel and (self.ctx.layout.root / rel).exists()),
                }
            )
        for row in lyrics:
            rel = str(row.get("storage_relpath", "") or "").strip()
            out.append(
                {
                    "item_type": "lyrics",
                    "item_type_label": "歌词",
                    "item_id": str(row.get("lyrics_id", "") or ""),
                    "file_name": str(row.get("file_name", "") or ""),
                    "title": str(row.get("lyrics_title", "") or ""),
                    "artist": str(row.get("lyrics_artist", "") or ""),
                    "album": str(row.get("lyrics_album", "") or ""),
                    "storage_relpath": rel,
                    "deleted_at": str(row.get("deleted_at", "") or ""),
                    "file_exists": bool(rel and (self.ctx.layout.root / rel).exists()),
                }
            )
        out.sort(key=lambda r: str(r.get("deleted_at", "")), reverse=True)
        return out[: max(1, int(limit))]

    def restore_tracks(self, track_ids: list[str]) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1arestore_tracks\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            count = LibraryOpsService(repo).restore_tracks(track_ids)
            if count > 0:
                self._append_undo(repo, "restore_tracks", {"track_ids": track_ids})
                self._log(f"restore_tracks count={count}")
            return count

    def purge_deleted_track_files(self, track_ids: list[str]) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1apurge_deleted_track_files\u3002"""
        ids = [str(v) for v in track_ids if str(v)]
        if not ids:
            return 0
        deleted_rows = {str(r.get("track_id", "")): r for r in self.list_deleted_tracks(limit=2_000_000, include_missing=True)}
        processed = 0
        for tid in ids:
            row = deleted_rows.get(tid) or {}
            if not row:
                continue
            processed += 1
            rel = str(row.get("storage_relpath", "") or "")
            if not rel:
                continue
            target = self.ctx.layout.root / rel
            try:
                target.unlink(missing_ok=True)
            except Exception:
                continue
        if processed > 0:
            self._redo_actions.clear()
            self._log(f"purge_deleted_track_files count={processed}")
        return processed

    def purge_deleted_lyrics_files(self, lyrics_ids: list[str]) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1apurge_deleted_lyrics_files\u3002"""
        ids = [str(v) for v in lyrics_ids if str(v)]
        if not ids:
            return 0
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            rows = LibraryRepository(conn).list_deleted_lyrics(limit=2_000_000)
        deleted_rows = {str(r.get("lyrics_id", "")): r for r in rows}
        processed = 0
        for lid in ids:
            row = deleted_rows.get(lid) or {}
            if not row:
                continue
            processed += 1
            rel = str(row.get("storage_relpath", "") or "")
            if not rel:
                continue
            target = self.ctx.layout.root / rel
            try:
                target.unlink(missing_ok=True)
            except Exception:
                continue
        if processed > 0:
            self._redo_actions.clear()
            self._log(f"purge_deleted_lyrics_files count={processed}")
        return processed

    def restore_deleted_items(self, items: list[dict]) -> dict:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1arestore_deleted_items\u3002"""
        track_ids = [str(i.get("item_id", "")) for i in items if str(i.get("item_type", "")) == "track" and i.get("item_id")]
        lyrics_ids = [str(i.get("item_id", "")) for i in items if str(i.get("item_type", "")) == "lyrics" and i.get("item_id")]
        track_count = self.restore_tracks(track_ids) if track_ids else 0
        lyrics_count = self.restore_lyrics(lyrics_ids) if lyrics_ids else 0
        return {"tracks": track_count, "lyrics": lyrics_count, "total": track_count + lyrics_count}

    def purge_deleted_item_files(self, items: list[dict]) -> dict:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1apurge_deleted_item_files\u3002"""
        track_ids = [str(i.get("item_id", "")) for i in items if str(i.get("item_type", "")) == "track" and i.get("item_id")]
        lyrics_ids = [str(i.get("item_id", "")) for i in items if str(i.get("item_type", "")) == "lyrics" and i.get("item_id")]
        track_count = self.purge_deleted_track_files(track_ids) if track_ids else 0
        lyrics_count = self.purge_deleted_lyrics_files(lyrics_ids) if lyrics_ids else 0
        return {"tracks": track_count, "lyrics": lyrics_count, "total": track_count + lyrics_count}

    def delete_deleted_items_metadata(self, items: list[dict]) -> dict:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1adelete_deleted_items_metadata\u3002"""
        track_ids = [str(i.get("item_id", "")) for i in items if str(i.get("item_type", "")) == "track" and i.get("item_id")]
        lyrics_ids = [str(i.get("item_id", "")) for i in items if str(i.get("item_type", "")) == "lyrics" and i.get("item_id")]
        track_count = 0
        lyrics_count = 0
        with self.ctx.db.session() as conn:
            if track_ids:
                placeholders = ",".join("?" for _ in track_ids)
                cursor = conn.execute(
                    f"DELETE FROM tracks WHERE track_id IN ({placeholders}) AND deleted_at IS NOT NULL",
                    tuple(track_ids),
                )
                track_count = int(cursor.rowcount or 0)
            if lyrics_ids:
                placeholders = ",".join("?" for _ in lyrics_ids)
                cursor = conn.execute(
                    f"DELETE FROM lyrics WHERE lyrics_id IN ({placeholders}) AND deleted_at IS NOT NULL",
                    tuple(lyrics_ids),
                )
                lyrics_count = int(cursor.rowcount or 0)
        if track_count > 0 or lyrics_count > 0:
            self._redo_actions.clear()
            self._log(f"delete_deleted_items_metadata tracks={track_count} lyrics={lyrics_count}")
        return {"tracks": track_count, "lyrics": lyrics_count, "total": track_count + lyrics_count}

    def update_tracks_fields(self, track_ids: list[str], fields: dict[str, object]) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aupdate_tracks_fields\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            before = repo.get_tracks_by_ids(track_ids)
            count = LibraryOpsService(repo).update_tracks_fields(track_ids, fields)
            if count > 0:
                rollback_values = []
                for row in before:
                    rollback_values.append(
                        {
                            "track_id": row["track_id"],
                            "file_name": row.get("file_name"),
                            "title": row.get("title"),
                            "artist": row.get("artist"),
                            "album": row.get("album"),
                            "language_kind": row.get("language_kind"),
                            "preference_level": row.get("preference_level"),
                        }
                    )
                self._append_undo(
                    repo,
                    "update_tracks_fields",
                    {
                        "track_ids": track_ids,
                        "applied_fields": dict(fields),
                        "rollback_values": rollback_values,
                    },
                )
                self._log(f"update_tracks_fields count={count} fields={list(fields.keys())}")
            return count

    def list_import_batches(self, limit: int = 200) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_import_batches\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).list_import_batches(limit)

    def get_import_batch_detail(self, import_batch_id: str) -> dict | None:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aget_import_batch_detail\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            batch = LibraryOpsService(LibraryRepository(conn)).get_import_batch(import_batch_id)
        if not batch:
            return None
        manifest = self.ctx.layout.root / "manifests" / "imports" / f"{import_batch_id}.json"
        file_states: list[dict] = []
        if manifest.exists():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                raw = payload.get("file_states") or []
                if isinstance(raw, list):
                    file_states = [r for r in raw if isinstance(r, dict)]
            except Exception:
                file_states = []
        result = dict(batch)
        result["file_states"] = file_states
        return result

    def list_playlists(self) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_playlists\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).list_playlists()

    def create_playlist(self, name: str, description: str = "") -> str:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1acreate_playlist\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            playlist_id = LibraryOpsService(repo).create_playlist(name, description)
            self._append_undo(
                repo,
                "create_playlist",
                {"playlist_id": playlist_id, "name": name, "description": description},
            )
            return playlist_id

    def delete_playlist(self, playlist_id: str) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1adelete_playlist\u3002"""
        if playlist_id == FAVORITES_PLAYLIST_ID:
            return 0
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            items = repo.list_playlist_items(playlist_id)
            details = [p for p in repo.list_playlists() if p.get("playlist_id") == playlist_id]
            meta = details[0] if details else {"name": "", "description": ""}
            count = LibraryOpsService(repo).delete_playlist(playlist_id)
            if count > 0:
                self._append_undo(
                    repo,
                    "delete_playlist",
                    {
                        "playlist_id": playlist_id,
                        "name": meta.get("name", ""),
                        "description": meta.get("description", ""),
                        "items": items,
                    },
                )
            return count

    def clear_playlist(self, playlist_id: str) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aclear_playlist\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            items_before = repo.list_playlist_items(playlist_id)
            count = LibraryOpsService(repo).clear_playlist(playlist_id)
            if count > 0:
                self._append_undo(
                    repo,
                    "clear_playlist",
                    {"playlist_id": playlist_id, "items_before": items_before},
                )
            return count

    def list_playlist_items(self, playlist_id: str) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_playlist_items\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryRepository(conn).list_playlist_items(playlist_id)

    def add_tracks_to_playlist(self, playlist_id: str, track_ids: list[str]) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aadd_tracks_to_playlist\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            count = LibraryOpsService(repo).add_tracks_to_playlist(playlist_id, track_ids)
            if count > 0:
                self._append_undo(repo, "add_tracks_to_playlist", {"playlist_id": playlist_id, "track_ids": track_ids})
            return count

    def remove_tracks_from_playlist(self, playlist_id: str, track_ids: list[str]) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aremove_tracks_from_playlist\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            items_before = repo.list_playlist_items(playlist_id)
            count = LibraryOpsService(repo).remove_tracks_from_playlist(playlist_id, track_ids)
            if count > 0:
                self._append_undo(
                    repo,
                    "remove_tracks_from_playlist",
                    {
                        "playlist_id": playlist_id,
                        "items_before": items_before,
                        "track_ids_removed": track_ids,
                    },
                )
            return count

    def reorder_playlist(self, playlist_id: str, ordered_track_ids: list[str]) -> None:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1areorder_playlist\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            before = [r["track_id"] for r in repo.list_playlist_items(playlist_id)]
            LibraryOpsService(repo).reorder_playlist(playlist_id, ordered_track_ids)
            self._append_undo(
                repo,
                "reorder_playlist",
                {
                    "playlist_id": playlist_id,
                    "ordered_track_ids_before": before,
                    "ordered_track_ids_after": ordered_track_ids,
                },
            )

    def update_playlist_entries(self, playlist_id: str, entries: dict[str, int]) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aupdate_playlist_entries\u3002"""
        if not entries:
            return 0
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            before_rows = repo.list_playlist_items(playlist_id)
            before_entries = {str(r.get("track_id", "")): int(r.get("entry", 0) or 0) for r in before_rows if r.get("track_id")}
            count = LibraryOpsService(repo).update_playlist_entries(playlist_id, entries)
            if count > 0:
                self._append_undo(
                    repo,
                    "update_playlist_entries",
                    {
                        "playlist_id": playlist_id,
                        "before_entries": before_entries,
                        "after_entries": {str(k): int(v) for k, v in entries.items() if k},
                    },
                )
            return count

    def add_to_favorites(self, track_ids: list[str]) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aadd_to_favorites\u3002"""
        return self.add_tracks_to_playlist(FAVORITES_PLAYLIST_ID, track_ids)

    def remove_from_favorites(self, track_ids: list[str]) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aremove_from_favorites\u3002"""
        return self.remove_tracks_from_playlist(FAVORITES_PLAYLIST_ID, track_ids)

    def list_tag_fields(self) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_tag_fields\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).list_tag_fields()

    def create_tag_field(self, tag_name: str) -> bool:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1acreate_tag_field\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            ok = LibraryOpsService(LibraryRepository(conn)).create_tag_field(tag_name)
            if ok:
                self._redo_actions.clear()
                self._log(f"create_tag_field name={tag_name}")
            return ok

    def delete_tag_field(self, tag_name: str) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1adelete_tag_field\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).delete_tag_field(tag_name)
            if count > 0:
                self._redo_actions.clear()
                self._log(f"delete_tag_field name={tag_name}")
            return count

    def update_track_tag_values(self, track_ids: list[str], tag_name: str, value: str) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aupdate_track_tag_values\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).update_track_tag_values(track_ids, tag_name, value)
            if count > 0:
                self._redo_actions.clear()
                self._log(f"update_track_tag_values tag={tag_name} count={count}")
            return count

    def list_lyrics(self, limit: int = 5000) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_lyrics\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).list_lyrics(limit)

    def set_primary_lyrics_for_track(self, track_id: str, lyrics_id: str | None) -> None:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aset_primary_lyrics_for_track\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            old_lyrics = repo.get_primary_lyrics_id_for_track(track_id)
            old_track_for_new = repo.get_primary_track_id_for_lyrics(lyrics_id) if lyrics_id else None
            LibraryOpsService(repo).set_primary_lyrics_for_track(track_id, lyrics_id)
            self._append_undo(
                repo,
                "set_primary_lyrics_for_track",
                {
                    "track_id": track_id,
                    "new_lyrics_id": lyrics_id,
                    "old_lyrics_id": old_lyrics,
                    "old_track_for_new_lyrics": old_track_for_new,
                },
            )
        self._redo_actions.clear()
        self._log(f"set_primary_lyrics_for_track track={track_id} lyrics={lyrics_id}")

    def set_primary_track_for_lyrics(self, lyrics_id: str, track_id: str | None) -> None:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aset_primary_track_for_lyrics\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            old_track = repo.get_primary_track_id_for_lyrics(lyrics_id)
            old_lyrics_for_new = repo.get_primary_lyrics_id_for_track(track_id) if track_id else None
            LibraryOpsService(repo).set_primary_track_for_lyrics(lyrics_id, track_id)
            self._append_undo(
                repo,
                "set_primary_track_for_lyrics",
                {
                    "lyrics_id": lyrics_id,
                    "new_track_id": track_id,
                    "old_track_id": old_track,
                    "old_lyrics_for_new_track": old_lyrics_for_new,
                },
            )
        self._redo_actions.clear()
        self._log(f"set_primary_track_for_lyrics lyrics={lyrics_id} track={track_id}")

    def merge_lyrics_for_review(
        self,
        primary_lyrics_id: str,
        secondary_lyrics_id: str,
        *,
        resolve_review_ids: list[str] | None = None,
    ) -> dict:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1amerge_lyrics_for_review\u3002"""
        primary_id = str(primary_lyrics_id or "").strip()
        secondary_id = str(secondary_lyrics_id or "").strip()
        if not primary_id or not secondary_id or primary_id == secondary_id:
            raise ValueError("invalid_lyrics_merge_target")

        review_ids = [str(v).strip() for v in (resolve_review_ids or []) if str(v).strip()]

        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            rows = repo.get_lyrics_by_ids([primary_id, secondary_id])
            by_id = {str(r.get("lyrics_id", "")): r for r in rows}
            primary_row = by_id.get(primary_id)
            secondary_row = by_id.get(secondary_id)
            if not primary_row or not secondary_row:
                raise ValueError("lyrics_not_found")

            primary_rel = str(primary_row.get("storage_relpath", "") or "").strip()
            secondary_rel = str(secondary_row.get("storage_relpath", "") or "").strip()
            primary_path = self.ctx.layout.root / primary_rel
            secondary_path = self.ctx.layout.root / secondary_rel
            if not primary_rel or not secondary_rel:
                raise ValueError("lyrics_storage_relpath_missing")

            primary_text = ""
            secondary_text = ""
            try:
                if primary_path.exists():
                    primary_text = primary_path.read_text(encoding="utf-8")
            except Exception:
                primary_text = ""
            try:
                if secondary_path.exists():
                    secondary_text = secondary_path.read_text(encoding="utf-8")
            except Exception:
                secondary_text = ""

            merged_text = _merge_lyrics_text(primary_text, secondary_text)
            if not merged_text:
                merged_text = (primary_text or secondary_text or "").strip()
            merged_hash = sha1_text(merged_text)
            hash_owner = repo.get_lyrics_by_text_hash(merged_hash)
            if hash_owner and str(hash_owner.get("lyrics_id", "") or "") not in {primary_id, secondary_id}:
                raise ValueError("lyrics_hash_conflict")

            affected_track_ids = {
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT track_id FROM track_lyrics WHERE lyrics_id IN (?, ?)",
                    (primary_id, secondary_id),
                ).fetchall()
                if str(r[0] or "").strip()
            }
            track_links_before: list[dict] = []
            if affected_track_ids:
                placeholders = ",".join("?" for _ in affected_track_ids)
                link_rows = conn.execute(
                    f"""
                    SELECT track_id, lyrics_id, confidence, match_method, is_primary, created_at, ext_json
                    FROM track_lyrics
                    WHERE track_id IN ({placeholders})
                    """,
                    tuple(sorted(affected_track_ids)),
                ).fetchall()
                track_links_before = [dict(r) for r in link_rows]

            review_status_before: dict[str, dict] = {}
            if review_ids:
                placeholders = ",".join("?" for _ in review_ids)
                review_rows = conn.execute(
                    f"SELECT review_id, status, resolved_at FROM review_queue WHERE review_id IN ({placeholders})",
                    tuple(review_ids),
                ).fetchall()
                review_status_before = {
                    str(r["review_id"]): {"status": str(r["status"] or ""), "resolved_at": r["resolved_at"]}
                    for r in review_rows
                }

            line_count = len([line for line in merged_text.splitlines() if str(line or "").strip()])
            merged_title = str(primary_row.get("lyrics_title", "") or "").strip() or str(secondary_row.get("lyrics_title", "") or "").strip()
            merged_artist = str(primary_row.get("lyrics_artist", "") or "").strip() or str(secondary_row.get("lyrics_artist", "") or "").strip()
            merged_album = str(primary_row.get("lyrics_album", "") or "").strip() or str(secondary_row.get("lyrics_album", "") or "").strip()
            merged_author = str(primary_row.get("lyrics_author", "") or "").strip() or str(secondary_row.get("lyrics_author", "") or "").strip()

            primary_path.parent.mkdir(parents=True, exist_ok=True)
            primary_path.write_text(merged_text, encoding="utf-8")
            conn.execute(
                """
                UPDATE lyrics
                SET text_hash = ?, raw_encoding = ?, lyrics_title = ?, lyrics_artist = ?, lyrics_album = ?,
                    lyrics_author = ?, line_count = ?, deleted_at = NULL
                WHERE lyrics_id = ?
                """,
                (
                    merged_hash,
                    "utf-8",
                    merged_title,
                    merged_artist,
                    merged_album,
                    merged_author,
                    int(line_count),
                    primary_id,
                ),
            )

            secondary_links = conn.execute(
                """
                SELECT track_id, confidence, match_method, is_primary, ext_json
                FROM track_lyrics
                WHERE lyrics_id = ?
                """,
                (secondary_id,),
            ).fetchall()
            now = _utc_now_iso()
            for link in secondary_links:
                track_id = str(link["track_id"] or "")
                if not track_id:
                    continue
                is_primary = int(link["is_primary"] or 0)
                if is_primary:
                    conn.execute("UPDATE track_lyrics SET is_primary = 0 WHERE track_id = ?", (track_id,))
                conn.execute(
                    """
                    INSERT INTO track_lyrics(track_id, lyrics_id, confidence, match_method, is_primary, created_at, ext_json)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(track_id, lyrics_id) DO UPDATE SET
                      confidence = excluded.confidence,
                      match_method = excluded.match_method,
                      is_primary = CASE WHEN excluded.is_primary > track_lyrics.is_primary THEN excluded.is_primary ELSE track_lyrics.is_primary END,
                      created_at = excluded.created_at
                    """,
                    (
                        track_id,
                        primary_id,
                        float(link["confidence"] or 0.0),
                        str(link["match_method"] or "merge"),
                        1 if is_primary else 0,
                        now,
                        str(link["ext_json"] or "{}"),
                    ),
                )
            conn.execute("DELETE FROM track_lyrics WHERE lyrics_id = ?", (secondary_id,))
            conn.execute("UPDATE lyrics SET deleted_at = ? WHERE lyrics_id = ?", (now, secondary_id))

            if review_ids:
                repo.set_reviews_status(review_ids, "ignored")

            rows_after = repo.get_lyrics_by_ids([primary_id, secondary_id])
            by_id_after = {str(r.get("lyrics_id", "")): dict(r) for r in rows_after}
            track_links_after: list[dict] = []
            if affected_track_ids:
                placeholders = ",".join("?" for _ in affected_track_ids)
                link_rows_after = conn.execute(
                    f"""
                    SELECT track_id, lyrics_id, confidence, match_method, is_primary, created_at, ext_json
                    FROM track_lyrics
                    WHERE track_id IN ({placeholders})
                    """,
                    tuple(sorted(affected_track_ids)),
                ).fetchall()
                track_links_after = [dict(r) for r in link_rows_after]

            review_status_after: dict[str, dict] = {}
            if review_ids:
                placeholders = ",".join("?" for _ in review_ids)
                review_rows_after = conn.execute(
                    f"SELECT review_id, status, resolved_at FROM review_queue WHERE review_id IN ({placeholders})",
                    tuple(review_ids),
                ).fetchall()
                review_status_after = {
                    str(r["review_id"]): {"status": str(r["status"] or ""), "resolved_at": r["resolved_at"]}
                    for r in review_rows_after
                }

            self._append_undo(
                repo,
                "merge_lyrics_for_review",
                {
                    "primary_lyrics_id": primary_id,
                    "secondary_lyrics_id": secondary_id,
                    "primary_storage_relpath": primary_rel,
                    "secondary_storage_relpath": secondary_rel,
                    "before": {
                        "primary_row": dict(primary_row),
                        "secondary_row": dict(secondary_row),
                        "primary_text": primary_text,
                        "secondary_text": secondary_text,
                        "track_links": track_links_before,
                        "review_status": review_status_before,
                    },
                    "after": {
                        "primary_row": by_id_after.get(primary_id, {}),
                        "secondary_row": by_id_after.get(secondary_id, {}),
                        "primary_text": merged_text,
                        "secondary_text": secondary_text,
                        "track_links": track_links_after,
                        "review_status": review_status_after,
                    },
                },
            )

        self._redo_actions.clear()
        self._log(f"merge_lyrics_for_review primary={primary_id} secondary={secondary_id}")
        return {
            "primary_lyrics_id": primary_id,
            "secondary_lyrics_id": secondary_id,
            "line_count": int(line_count),
            "merged_text_hash": merged_hash,
            "resolved_reviews": len(review_ids),
        }

    def update_lyrics_author(self, lyrics_ids: list[str], author: str) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aupdate_lyrics_author\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).update_lyrics_author(lyrics_ids, author)
        if count > 0:
            self._redo_actions.clear()
            self._log(f"update_lyrics_author count={count}")
        return count

    def update_lyrics_fields(self, lyrics_ids: list[str], fields: dict[str, object]) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aupdate_lyrics_fields\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            before = repo.get_lyrics_by_ids(lyrics_ids)
            count = LibraryOpsService(repo).update_lyrics_fields(lyrics_ids, fields)
            if count > 0:
                rollback_values = []
                for row in before:
                    rollback_values.append(
                        {
                            "lyrics_id": row.get("lyrics_id"),
                            "file_name": row.get("file_name"),
                            "lyrics_title": row.get("lyrics_title"),
                            "lyrics_artist": row.get("lyrics_artist"),
                            "lyrics_album": row.get("lyrics_album"),
                            "lyrics_author": row.get("lyrics_author"),
                        }
                    )
                self._append_undo(
                    repo,
                    "update_lyrics_fields",
                    {
                        "lyrics_ids": lyrics_ids,
                        "applied_fields": dict(fields),
                        "rollback_values": rollback_values,
                    },
                )
        if count > 0:
            self._redo_actions.clear()
            self._log(f"update_lyrics_fields count={count} fields={list(fields.keys())}")
        return count

    def delete_lyrics(self, lyrics_ids: list[str]) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1adelete_lyrics\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            relpaths = LibraryOpsService(repo).delete_lyrics(lyrics_ids)
            if relpaths:
                self._append_undo(repo, "delete_lyrics", {"lyrics_ids": lyrics_ids, "relpaths": relpaths})
        if relpaths:
            self._redo_actions.clear()
            self._log(f"move_lyrics_to_trash count={len(relpaths)}")
        return len(relpaths)

    def restore_lyrics(self, lyrics_ids: list[str]) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1arestore_lyrics\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            count = LibraryOpsService(repo).restore_lyrics(lyrics_ids)
            if count > 0:
                self._append_undo(repo, "restore_lyrics", {"lyrics_ids": lyrics_ids})
                self._log(f"restore_lyrics count={count}")
            return count
