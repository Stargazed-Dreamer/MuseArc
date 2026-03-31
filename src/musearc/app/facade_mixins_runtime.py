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

class FacadeRuntimeMixin:
    """Facade mixin: runtime/fullscan/undo-redo workflows."""

    def read_logs(self) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aread_logs\u3002"""
        return read_action_logs(self.ctx.layout.root)

    def save_now(self) -> None:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1asave_now\u3002"""
        with self.ctx.db.session() as conn:
            conn.execute("SELECT 1")
        self._log("save_now")

    def create_fullscan_work(self, name: str) -> str:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1acreate_fullscan_work\u3002"""
        tracks = self.list_tracks(limit=2_000_000)
        track_ids = [row["track_id"] for row in tracks]
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            work_id = LibraryOpsService(repo).create_fullscan_work(name, track_ids)
            self._append_undo(
                repo,
                "create_fullscan_work",
                {"work_id": work_id, "name": name, "track_ids": track_ids},
            )
            return work_id

    def list_fullscan_works(self) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_fullscan_works\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).list_fullscan_works()

    def get_fullscan_work_items(self, work_id: str, limit: int = 200000) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aget_fullscan_work_items\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            return LibraryOpsService(LibraryRepository(conn)).get_fullscan_work_items(work_id, limit)

    def remove_fullscan_items(self, work_id: str, track_ids: list[str]) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aremove_fullscan_items\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).remove_fullscan_items(work_id, track_ids)
            if count > 0:
                self._redo_actions.clear()
            return count

    def update_fullscan_items_status(self, work_id: str, track_ids: list[str], status: str) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aupdate_fullscan_items_status\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).update_fullscan_items_status(work_id, track_ids, status)
            if count > 0:
                self._redo_actions.clear()
            return count

    def delete_fullscan_work(self, work_id: str) -> int:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1adelete_fullscan_work\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            count = LibraryOpsService(LibraryRepository(conn)).delete_fullscan_work(work_id)
            if count > 0:
                self._redo_actions.clear()
            return count

    def list_undo_actions(self, limit: int = 50) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_undo_actions\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            rows = LibraryRepository(conn).list_undo_actions(limit)
            return [
                {
                    "action_id": row.action_id,
                    "action_type": row.action_type,
                    "payload": row.payload,
                    "created_at": row.created_at,
                }
                for row in rows
            ]

    def list_redo_actions(self) -> list[dict]:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_redo_actions\u3002"""
        return list(self._redo_actions)

    def list_action_timeline(self, limit: int = 200) -> dict:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1alist_action_timeline\u3002"""
        undo_desc = self.list_undo_actions(limit)
        applied = list(reversed(undo_desc))
        undone = list(reversed(self._redo_actions))[:limit]
        history = applied + undone
        return {"history": history, "current_index": len(applied) - 1}

    def undo_last_action(self) -> str:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aundo_last_action\u3002"""
        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)
            action = repo.pop_latest_undo_action()
            if not action:
                return "no_action"

            payload = action.payload
            t = action.action_type
            self._redo_actions.append(
                {
                    "action_id": action.action_id,
                    "action_type": t,
                    "payload": payload,
                    "created_at": action.created_at,
                }
            )

            if t == "soft_delete_tracks":
                track_ids = payload.get("track_ids", [])
                repo.restore_tracks(track_ids)
                if payload.get("mode", "move_linked_lyrics") == "move_linked_lyrics":
                    repo.restore_lyrics_for_tracks(track_ids)
                return "ok:restore_tracks"
            if t == "restore_tracks":
                LibraryOpsService(repo).delete_tracks(payload.get("track_ids", []), mode="move_linked_lyrics")
                return "ok:soft_delete_tracks"
            if t == "delete_lyrics":
                repo.restore_lyrics(payload.get("lyrics_ids", []))
                return "ok:delete_lyrics"
            if t == "restore_lyrics":
                repo.move_lyrics_to_trash(payload.get("lyrics_ids", []))
                return "ok:restore_lyrics"
            if t == "update_tracks_fields":
                for row in payload.get("rollback_values", []):
                    track_id = row.get("track_id")
                    patch = {k: v for k, v in row.items() if k != "track_id"}
                    if track_id:
                        repo.update_tracks_fields([track_id], patch)
                return "ok:update_tracks_fields"
            if t == "update_lyrics_fields":
                for row in payload.get("rollback_values", []):
                    lyrics_id = row.get("lyrics_id")
                    if not lyrics_id:
                        continue
                    patch = {
                        "file_name": row.get("file_name", ""),
                        "lyrics_title": row.get("lyrics_title", ""),
                        "lyrics_artist": row.get("lyrics_artist", ""),
                        "lyrics_album": row.get("lyrics_album", ""),
                        "lyrics_author": row.get("lyrics_author", ""),
                    }
                    repo.update_lyrics_fields([lyrics_id], patch)
                return "ok:update_lyrics_fields"
            if t == "set_primary_lyrics_for_track":
                track_id = str(payload.get("track_id", "") or "")
                old_lyrics_id = payload.get("old_lyrics_id")
                new_lyrics_id = payload.get("new_lyrics_id")
                old_track_for_new = payload.get("old_track_for_new_lyrics")
                if track_id:
                    repo.set_primary_lyrics_for_track(track_id, old_lyrics_id)
                if new_lyrics_id:
                    if old_track_for_new and str(old_track_for_new) != track_id:
                        repo.set_primary_lyrics_for_track(str(old_track_for_new), str(new_lyrics_id))
                    elif not old_track_for_new:
                        repo.set_primary_track_for_lyrics(str(new_lyrics_id), None)
                return "ok:set_primary_lyrics_for_track"
            if t == "set_primary_track_for_lyrics":
                lyrics_id = str(payload.get("lyrics_id", "") or "")
                old_track_id = payload.get("old_track_id")
                new_track_id = payload.get("new_track_id")
                old_lyrics_for_new = payload.get("old_lyrics_for_new_track")
                if lyrics_id:
                    repo.set_primary_track_for_lyrics(lyrics_id, old_track_id)
                if new_track_id:
                    if old_lyrics_for_new and str(old_lyrics_for_new) != lyrics_id:
                        repo.set_primary_lyrics_for_track(str(new_track_id), str(old_lyrics_for_new))
                    elif not old_lyrics_for_new:
                        repo.set_primary_lyrics_for_track(str(new_track_id), None)
                return "ok:set_primary_track_for_lyrics"
            if t == "create_playlist":
                repo.delete_playlist(payload.get("playlist_id", ""))
                return "ok:delete_playlist"
            if t == "delete_playlist":
                playlist_id = payload.get("playlist_id") or new_id("pl")
                repo.create_playlist(playlist_id, payload.get("name", ""), payload.get("description", ""))
                items = payload.get("items", [])
                ordered = [it.get("track_id") for it in sorted(items, key=lambda x: int(x.get("position", 0))) if it.get("track_id")]
                repo.add_tracks_to_playlist(playlist_id, ordered)
                if ordered:
                    repo.reorder_playlist(playlist_id, ordered)
                entry_patch = {str(it.get("track_id")): int(it.get("entry", idx)) for idx, it in enumerate(items) if it.get("track_id")}
                if entry_patch:
                    repo.update_playlist_entries(playlist_id, entry_patch)
                return "ok:restore_playlist"
            if t == "add_tracks_to_playlist":
                repo.remove_tracks_from_playlist(payload.get("playlist_id", ""), payload.get("track_ids", []))
                return "ok:remove_tracks_from_playlist"
            if t == "remove_tracks_from_playlist":
                playlist_id = payload.get("playlist_id", "")
                items_before = payload.get("items_before", [])
                ordered = [it.get("track_id") for it in sorted(items_before, key=lambda x: int(x.get("position", 0))) if it.get("track_id")]
                repo.add_tracks_to_playlist(playlist_id, ordered)
                if ordered:
                    repo.reorder_playlist(playlist_id, ordered)
                entry_patch = {
                    str(it.get("track_id")): int(it.get("entry", idx))
                    for idx, it in enumerate(items_before)
                    if it.get("track_id")
                }
                if entry_patch:
                    repo.update_playlist_entries(playlist_id, entry_patch)
                return "ok:restore_playlist_items"
            if t == "clear_playlist":
                playlist_id = payload.get("playlist_id", "")
                items_before = payload.get("items_before", [])
                ordered = [it.get("track_id") for it in sorted(items_before, key=lambda x: int(x.get("position", 0))) if it.get("track_id")]
                repo.add_tracks_to_playlist(playlist_id, ordered)
                if ordered:
                    repo.reorder_playlist(playlist_id, ordered)
                entry_patch = {
                    str(it.get("track_id")): int(it.get("entry", idx))
                    for idx, it in enumerate(items_before)
                    if it.get("track_id")
                }
                if entry_patch:
                    repo.update_playlist_entries(playlist_id, entry_patch)
                return "ok:restore_playlist_items"
            if t == "reorder_playlist":
                repo.reorder_playlist(payload.get("playlist_id", ""), payload.get("ordered_track_ids_before", []))
                return "ok:reorder_playlist"
            if t == "update_playlist_entries":
                repo.update_playlist_entries(payload.get("playlist_id", ""), payload.get("before_entries", {}))
                return "ok:update_playlist_entries"
            if t == "create_fullscan_work":
                repo.delete_fullscan_work(payload.get("work_id", ""))
                return "ok:delete_fullscan_work"
            if t == "resolve_reviews":
                repo.set_reviews_status(payload.get("review_ids", []), "pending")
                return "ok:restore_reviews_pending"

            return f"unsupported:{t}"

    def redo_last_action(self) -> str:
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aredo_last_action\u3002"""
        if not self._redo_actions:
            return "no_action"

        action = self._redo_actions.pop()
        payload = action.get("payload", {})
        t = str(action.get("action_type", ""))

        with self.ctx.db.session() as conn:
            from musearc.infra.db.repositories import LibraryRepository

            repo = LibraryRepository(conn)

            if t == "soft_delete_tracks":
                mode = payload.get("mode", "move_linked_lyrics")
                LibraryOpsService(repo).delete_tracks(payload.get("track_ids", []), mode=mode)
            elif t == "restore_tracks":
                LibraryOpsService(repo).restore_tracks(payload.get("track_ids", []))
            elif t == "delete_lyrics":
                repo.move_lyrics_to_trash(payload.get("lyrics_ids", []))
            elif t == "restore_lyrics":
                repo.restore_lyrics(payload.get("lyrics_ids", []))
            elif t == "update_tracks_fields":
                repo.update_tracks_fields(payload.get("track_ids", []), payload.get("applied_fields", {}))
            elif t == "update_lyrics_fields":
                repo.update_lyrics_fields(payload.get("lyrics_ids", []), payload.get("applied_fields", {}))
            elif t == "set_primary_lyrics_for_track":
                repo.set_primary_lyrics_for_track(payload.get("track_id", ""), payload.get("new_lyrics_id"))
            elif t == "set_primary_track_for_lyrics":
                repo.set_primary_track_for_lyrics(payload.get("lyrics_id", ""), payload.get("new_track_id"))
            elif t == "create_playlist":
                repo.create_playlist(payload.get("playlist_id", new_id("pl")), payload.get("name", ""), payload.get("description", ""))
            elif t == "delete_playlist":
                repo.delete_playlist(payload.get("playlist_id", ""))
            elif t == "add_tracks_to_playlist":
                repo.add_tracks_to_playlist(payload.get("playlist_id", ""), payload.get("track_ids", []))
            elif t == "remove_tracks_from_playlist":
                repo.remove_tracks_from_playlist(payload.get("playlist_id", ""), payload.get("track_ids_removed", []))
            elif t == "clear_playlist":
                repo.clear_playlist(payload.get("playlist_id", ""))
            elif t == "reorder_playlist":
                repo.reorder_playlist(payload.get("playlist_id", ""), payload.get("ordered_track_ids_after", []))
            elif t == "update_playlist_entries":
                repo.update_playlist_entries(payload.get("playlist_id", ""), payload.get("after_entries", {}))
            elif t == "create_fullscan_work":
                repo.create_fullscan_work(
                    payload.get("work_id", new_id("work")),
                    payload.get("name", ""),
                    payload.get("track_ids", []),
                )
            elif t == "resolve_reviews":
                repo.set_reviews_status(payload.get("review_ids", []), payload.get("status_after", "resolved"))
            else:
                self._redo_actions.append(action)
                return f"unsupported_redo:{t}"

            repo.append_undo_action(new_id("undo"), t, payload, self._undo_keep())
            return f"ok:redo:{t}"

    def get_runtime_config(self):
        """\u0046\u0061\u0063\u0061\u0064\u0065 \u65b9\u6cd5\uff1aget_runtime_config\u3002"""
        return self.ctx.runtime_config
