# Import Pipeline (Current)

## Scan
1. Scan source folder into audio candidates and lyrics candidates.
2. Load resume state (if any) and skip already processed files.
3. Build per-file status rows for progress/reporting.

## Audio pipeline
1. Early path-level skip before heavy work:
   - path already exists in library (including soft-deleted tracks),
   - path matches historical skipped-audio index,
   - path already has a pending review item.
2. Probe media metadata.
3. Reject too-short files to file-issue review.
4. Loudness normalize to `-14 LUFS`, then generate fingerprint.
5. Run duplicate decision (keep existing / review / keep new).
6. Archive copy + compute `source_sha256` in the same pass.
7. Resolve `source_sha256` conflicts:
   - live record -> skip,
   - deleted record -> review for re-import.
8. Any skipped audio path is persisted for future fast exclusion.

## Lyrics pipeline
1. Early path-level skip:
   - path exists in historical processed-lyrics index,
   - path already has a pending review item.
2. Read text and unescape HTML entities.
3. Instrumental placeholder lyrics:
   - skip lyrics import,
   - try to mark matched track language as `instrumental`.
4. `text_hash` dedupe:
   - deleted lyrics hit -> review,
   - live lyrics hit -> skip directly (no duplicate review item).
5. New lyrics insert.
6. Lyrics-track matching:
   - try current-batch tracks first,
   - fallback to full-library tracks,
   - if still low confidence, enqueue review with suggestions
     (batch-first suggestions, then library suggestions).

## Review de-duplication
1. Import will not create duplicate pending review items for the same source path.
2. Lyrics text duplicates are skipped directly to avoid repeated review noise.

## Cancellation & resume
1. Cancel modes:
   - stop and keep progress,
   - stop and rollback all changes in this run.
2. Resume is supported from the saved import state.

## Path index files
1. `manifests/imports/skipped_audio_paths.json`
2. `manifests/imports/seen_lyrics_paths.json`
