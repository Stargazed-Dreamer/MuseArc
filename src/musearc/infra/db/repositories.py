from __future__ import annotations

"""Repository entry class."""

import json
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from musearc.core.models import LyricsInsert, ReviewItem, TrackInsert, UndoAction


from musearc.infra.db.repositories_common import (
    DEFAULT_TAG_FIELD,
    DEFAULT_TAG_FIELDS,
    FAVORITES_PLAYLIST_ID,
    FAVORITES_PLAYLIST_NAME,
    _normalize_tags,
    _placeholders,
    _safe_json_loads,
    _utc_now_iso,
)
from musearc.infra.db.repositories_mixins_meta_import import RepositoryMetaImportMixin
from musearc.infra.db.repositories_mixins_tracks_lyrics import RepositoryTracksLyricsMixin
from musearc.infra.db.repositories_mixins_tracks_maintenance import RepositoryTracksMaintenanceMixin
from musearc.infra.db.repositories_mixins_playlists import RepositoryPlaylistsMixin
from musearc.infra.db.repositories_mixins_ops import RepositoryOpsMixin

class LibraryRepository(RepositoryMetaImportMixin, RepositoryTracksLyricsMixin, RepositoryTracksMaintenanceMixin, RepositoryPlaylistsMixin, RepositoryOpsMixin):
    def __init__(self, conn):
        """\u4ed3\u50a8\u65b9\u6cd5\uff1a__init__\u3002"""
        self.conn = conn

    def _enrich_track_rows(self, rows) -> list[dict]:
        """\u4ed3\u50a8\u65b9\u6cd5\uff1a_enrich_track_rows\u3002"""
        out: list[dict] = []
        for row in rows:
            item = dict(row)
            payload, tags = _normalize_tags(item.get("ext_json"))
            item["ext_json"] = json.dumps(payload, ensure_ascii=False)
            item["tags"] = tags
            for tag_name, tag_value in tags.items():
                item[f"tag:{tag_name}"] = tag_value
            item["format"] = str(item.get("storage_format") or item.get("source_ext") or "").replace(".", "").lower()
            out.append(item)
        return out
