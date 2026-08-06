from __future__ import annotations

"""??????????

???? main_window_pages.py ?????????????
- main_window_pages_tracks.py
- main_window_pages_ops.py
- main_window_pages_lyrics.py
"""

from musearc.ui.main_window_pages_lyrics import LyricsManagementPage
from musearc.ui.main_window_pages_ops import FullScanPage, TagManagementPage, TrashPage
from musearc.ui.main_window_pages_tracks import PlaylistPage, TracksPage

__all__ = [
    "TracksPage",
    "PlaylistPage",
    "FullScanPage",
    "TrashPage",
    "TagManagementPage",
    "LyricsManagementPage",
]
