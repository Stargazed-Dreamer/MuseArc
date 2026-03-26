# Next Iteration Architecture Notes

Date: 2026-03-25

## Goals
1. Replace custom sort strip with true table-header-based sorting controls.
2. Stabilize multi-select interaction model and editing semantics.
3. Keep audio in original format at import time; expose format field.
4. Add tag management and dynamic tag columns in track tables.
5. Add lyrics management view and mapping workflow.
6. Add lightweight action logs (max 10 entries, setting-controlled).

## Sorting Header Model
- Use table header as sorting controls.
- Header click cycles state: asc -> desc -> off.
- Sort priority follows header visual order (left to right).
- Header section move changes both column order and sort priority.
- Header section resize remains interactive.

## Track Row Data Contract
Each row in track-like tables should include:
- `track_id`
- base fields (`file_name`, `title`, `artist`, `album`, etc.)
- `storage_format` (display label: 格式)
- favorite flag `is_favorite`
- custom order `entry` (UI label: 自定义排序)
- dynamic tags flattened as `tag:<name>` plus dict `tags`

## Tag Storage
- Keep a dedicated tag field list in DB table `tag_fields`.
- Keep per-track values in `tracks.ext_json.tags` dictionary.
- Default tag field exists: `备注`.

## Lyrics Mapping Rules
- `track_lyrics.is_primary=1` is primary mapping for one track.
- Lyrics management table shows one mapped track (if any primary link exists).
- Remapping should replace previous primary link for that lyrics item.

## Export Config
- Add a per-track export config dialog with a format selector per selected track.
- Bulk actions in dialog:
  - apply original format
  - apply one format to all

## Logging
- App-level log file: `manifests/app_logs.json`.
- Only active if setting enabled.
- Keep latest 10 entries.

## Save Behavior (interim plan)
- Keep DB correctness first: preserve current transactional writes.
- Add explicit Ctrl+S command to flush and write a save checkpoint.
- Add configurable autosave tick in UI; first iteration uses checkpoint/log flush.
