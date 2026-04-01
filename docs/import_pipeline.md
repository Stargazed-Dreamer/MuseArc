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
   - Metadata priority is `ID3/container tags > filename fallback`.
   - Suspected mojibake tags are repaired with multi-encoding fallback.
3. Reject too-short files to file-issue review.
4. Loudness normalize to `-14 LUFS`, then generate fingerprint.
5. Run duplicate decision (keep existing / review / keep new).
6. Archive copy + compute `source_sha256` in the same pass.
7. Resolve `source_sha256` conflicts:
   - live record -> skip,
   - deleted record -> review for re-import.
8. Any skipped audio path is persisted for future fast exclusion.
9. Duplicate replacement prefers better quality source:
   - format rank + quality score are combined,
   - VBR formats without bitrate use `file_size/duration` estimate to avoid MP3-biased decisions.

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
3. Lyrics review supports merging the two currently-previewed lyrics:
   merged text is written back to the first entry, second entry is moved to trash metadata,
   and the operation is undoable.

## Cancellation & resume
1. Cancel modes:
   - stop and keep progress,
   - stop and rollback all changes in this run.
2. Resume is supported from the saved import state.

## Path index files
1. `manifests/imports/skipped_audio_paths.json`
2. `manifests/imports/seen_lyrics_paths.json`

## Post-import repair tool
Menu: `More -> Use ID3 and lyrics to update track metadata`

- Runs on a selected full-scan work.
- Update policy:
  - use ID3 when available (overwrite),
  - fallback to filename + lyrics metadata,
  - otherwise skip with reason.
