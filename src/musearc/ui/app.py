from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .theme import apply_musearc_theme


def run_ui(library_path: str | None = None) -> int:
    app = QApplication(sys.argv)
    apply_musearc_theme(app)
    window = MainWindow(library_path=library_path)
    window.show()
    return app.exec()
