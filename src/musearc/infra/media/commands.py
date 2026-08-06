from __future__ import annotations

from musearc.core.exceptions import MediaError


class MediaCommandError(MediaError):
    """Media backend error (decode/encode/probe/fingerprint)."""
