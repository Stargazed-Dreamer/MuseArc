from enum import StrEnum


class FileHealth(StrEnum):
    OK = "ok"
    CORRUPTED = "corrupted"
    TOO_SHORT = "too_short"
    FAKE = "fake"


class DuplicateDecision(StrEnum):
    KEEP_NEW = "keep_new"
    KEEP_EXISTING = "keep_existing"
    KEEP_BOTH = "keep_both"
    REVIEW = "review"


class ReviewKind(StrEnum):
    DUPLICATE = "duplicate"
    LYRICS_MATCH = "lyrics_match"
    METADATA_CONFLICT = "metadata_conflict"
    FILE_ISSUE = "file_issue"


class TrackKind(StrEnum):
    MAIN = "main"
    LIVE = "live"
    REMIX = "remix"
    RADIO_EDIT = "radio_edit"
    COVER = "cover"
    UNKNOWN = "unknown"
