from __future__ import annotations

"""??????????

???? main_window_pages.py ?????????????
- main_window_pages_tracks.py
- main_window_pages_ops.py
- main_window_pages_lyrics.py
"""

from musearc.ui.main_window_pages_tracks import TracksPage, PlaylistPage
from musearc.ui.main_window_pages_ops import FullScanPage, TrashPage, TagManagementPage
from musearc.ui.main_window_pages_lyrics import LyricsManagementPage

__all__ = [
    "TracksPage",
    "PlaylistPage",
    "FullScanPage",
    "TrashPage",
    "TagManagementPage",
    "LyricsManagementPage",
]
