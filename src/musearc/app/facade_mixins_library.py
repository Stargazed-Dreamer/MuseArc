from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from musearc.app.action_log import append_action_log, read_action_logs
from musearc.core.hashing import sha1_text
from musearc.core.ids import new_id
from musearc.core.paths import ensure_parent, shard_relpath
from musearc.services.exporter import ExportService
from musearc.services.import_runtime import ImportControl, list_resume_states
from musearc.services.importer import ImportService
from musearc.services.library_ops import LibraryOpsService

FAVORITES_PLAYLIST_ID = "pl_favorites"

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
