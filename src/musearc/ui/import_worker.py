from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from musearc.app.facade import MuseArcFacade
from musearc.services.import_runtime import ImportControl


class ImportWorker(QObject):
    progress = Signal(dict)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, library_path: str, source_path: str):
        super().__init__()
        self.library_path = library_path
        self.source_path = source_path
        self.control = ImportControl()

    def request_cancel(self, mode: str) -> None:
        self.control.request_cancel(mode)

    def request_pause(self) -> None:
        self.control.request_pause()

    def request_resume(self) -> None:
        self.control.request_resume()

    def run(self) -> None:
        try:
            facade = MuseArcFacade(self.library_path)

            def _on_progress(p):
                self.progress.emit(
                    {
                        "import_batch_id": p.import_batch_id,
                        "source_path": p.source_path,
                        "stage": p.stage,
                        "current_file": p.current_file,
                        "scanned_files": p.scanned_files,
                        "processed_files": p.processed_files,
                        "imported_tracks": p.imported_tracks,
                        "duplicate_tracks": p.duplicate_tracks,
                        "imported_lyrics": p.imported_lyrics,
                        "matched_lyrics": p.matched_lyrics,
                        "review_items": p.review_items,
                        "errors": p.errors,
                        "resumed": p.resumed,
                        "paused": p.paused,
                        "file_states": list(p.file_states or []),
                    }
                )

            report = facade.import_from(self.source_path, control=self.control, progress_callback=_on_progress)
            self.finished.emit(report)
        except Exception as exc:
            self.failed.emit(str(exc))
