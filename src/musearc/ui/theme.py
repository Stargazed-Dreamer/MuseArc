from __future__ import annotations

from typing import Any


def apply_musearc_theme(app: Any) -> None:
    """统一应用浅色工业风主题。"""
    try:
        app.setStyle("Fusion")
    except Exception:
        pass

    try:
        from PySide6.QtGui import QColor, QPalette
    except Exception:
        return

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#e7ece8"))
    palette.setColor(QPalette.WindowText, QColor("#23302a"))
    palette.setColor(QPalette.Base, QColor("#f4f8f5"))
    palette.setColor(QPalette.AlternateBase, QColor("#dce5df"))
    palette.setColor(QPalette.ToolTipBase, QColor("#eef5f0"))
    palette.setColor(QPalette.ToolTipText, QColor("#223029"))
    palette.setColor(QPalette.Text, QColor("#223029"))
    palette.setColor(QPalette.Button, QColor("#d9e3dc"))
    palette.setColor(QPalette.ButtonText, QColor("#223029"))
    palette.setColor(QPalette.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.Link, QColor("#3f6f58"))
    palette.setColor(QPalette.Highlight, QColor("#4f7a63"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    app.setStyleSheet(
        """
        QWidget {
            background: #e7ece8;
            color: #23302a;
            selection-background-color: #4f7a63;
            selection-color: #ffffff;
        }

        QMenuBar {
            background: #d5dfd8;
            color: #23302a;
            border-bottom: 1px solid #b6c4bc;
        }
        QMenuBar::item { padding: 6px 10px; }
        QMenuBar::item:selected { background: #c4d2c9; }

        QMenu {
            background: #eef4f0;
            border: 1px solid #b7c5bd;
            padding: 4px;
        }
        QMenu::item { padding: 6px 22px; }
        QMenu::item:selected { background: #4f7a63; color: #ffffff; }

        QPushButton, QToolButton {
            background: #d8e2db;
            border: 1px solid #b3c1b9;
            border-radius: 3px;
            color: #23302a;
            padding: 5px 10px;
        }
        QPushButton:hover, QToolButton:hover {
            background: #c8d7ce;
            border-color: #97a99f;
        }
        QPushButton:pressed, QToolButton:pressed {
            background: #4f7a63;
            color: #ffffff;
        }
        QPushButton:disabled, QToolButton:disabled {
            color: #83958b;
            background: #e1e8e3;
            border-color: #c3cec8;
        }

        QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
            background: #f6faf7;
            color: #223029;
            border: 1px solid #b7c5bd;
            border-radius: 3px;
            padding: 4px 6px;
        }
        QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {
            border-color: #4f7a63;
        }

        QHeaderView::section {
            background: #d8e2db;
            border: 1px solid #b6c4bc;
            padding: 4px 6px;
            color: #304239;
        }

        QTableView, QTreeView, QListView {
            background: #f5f9f6;
            border: 1px solid #b6c4bc;
            alternate-background-color: #edf3ef;
        }

        QProgressBar {
            background: #edf3ef;
            border: 1px solid #b6c4bc;
            border-radius: 3px;
            text-align: center;
            color: #2f4238;
        }
        QProgressBar::chunk { background: #5a886f; }
        """
    )

