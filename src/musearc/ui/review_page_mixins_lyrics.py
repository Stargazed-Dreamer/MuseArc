from __future__ import annotations

"""审查页面-歌词审查区 Mixin。

该模块承载歌词审查分组、对比预览、绑定歌曲等逻辑，降低主页面复杂度。
"""

import re
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value, default: int = 0) -> int:
    if isinstance(value, (list, tuple, dict, set)):
        return default
    try:
        return int(value or 0)
    except Exception:
        return default


def _canonical_lyrics_name(file_name: str) -> str:
    stem = Path(str(file_name or "")).stem.casefold().strip()
    stem = re.sub(r"[\s._-]+", " ", stem)
    stem = re.sub(r"\s*[\(\[\uFF08\u3010].*?[\)\]\uFF09\u3011]\s*$", "", stem)
    return stem.strip()


def _lyrics_file_bracket_count(file_name: str) -> int:
    stem = Path(str(file_name or "")).stem
    return len(re.findall(r"[\(\[\uFF08\u3010].*?[\)\]\uFF09\u3011]", stem))


class _ClickableFrame(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            cb = getattr(self, '_click_cb', None)
            if callable(cb):
                cb()
            self.clicked.emit()
        super().mousePressEvent(event)


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            cb = getattr(self, '_click_cb', None)
            if callable(cb):
                cb()
            self.clicked.emit()
        super().mousePressEvent(event)


class ReviewPageLyricsMixin:
    def _fill_lyrics_tree(self, rows: list[dict]) -> None:
        """\u6784\u5efa\u6b4c\u8bcd\u5ba1\u67e5\u5206\u7ec4\u754c\u9762\uff08\u6bcf\u7ec4\u72ec\u7acb frame\uff09\u3002"""
        self._lyrics_group_controls.clear()
        self._clear_group_layout(self.lyrics_groups_layout)
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            groups[str(row.get("group_title", "") or row.get("group_key", "") or "未分组")].append(row)

        if not groups:
            empty = QLabel("暂无歌词待审查")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lyrics_groups_layout.addWidget(empty)
            self.lyrics_groups_layout.addStretch(1)
            return

        for group_key in sorted(groups.keys(), key=lambda s: s.casefold()):
            group_rows = list(groups[group_key])
            frame = QFrame()
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            frame.setStyleSheet("QFrame{background:#f8fbff;border:1px solid #d7e4f4;border-radius:8px;}")
            host = QVBoxLayout(frame)
            host.setContentsMargins(10, 10, 10, 10)
            host.setSpacing(8)

            title = QLabel(group_key)
            tfont = title.font()
            tfont.setBold(True)
            tfont.setPointSize(max(tfont.pointSize() + 4, 14))
            title.setFont(tfont)
            host.addWidget(title)

            header = QWidget()
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(6, 0, 6, 0)
            header_layout.setSpacing(8)
            for text, stretch, fixed in [
                ("保留", 0, 44),
                ("歌词文件", 3, 0),
                ("相似度", 0, 88),
                ("说明", 2, 0),
            ]:
                lbl = QLabel(text)
                font = lbl.font()
                font.setBold(True)
                lbl.setFont(font)
                if fixed > 0:
                    lbl.setFixedWidth(fixed)
                header_layout.addWidget(lbl, stretch)
            host.addWidget(header)

            row_controls: list[dict] = []
            for row in group_rows:
                row_ctrl = self._build_lyrics_row_widget(row)
                row_controls.append(row_ctrl)
                host.addWidget(row_ctrl["container"])

            row_ops_host = QWidget()
            row_ops = QHBoxLayout(row_ops_host)
            row_ops.setContentsMargins(0, 2, 0, 0)
            row_ops.setSpacing(8)
            btn_invert = QPushButton("反选")
            btn_same = QPushButton("这是相同歌词")
            btn_diff = QPushButton("这是不同歌词")
            btn_save = QPushButton("保存勾选的文件")
            btn_cancel = QPushButton("取消导入")
            row_ops.addWidget(btn_invert)
            row_ops.addWidget(btn_same)
            row_ops.addWidget(btn_diff)
            row_ops.addWidget(btn_save)
            row_ops.addWidget(btn_cancel)
            row_ops.addStretch(1)
            host.addWidget(row_ops_host)

            controls = {"group_key": group_key, "rows": row_controls}
            self._lyrics_group_controls[group_key] = controls
            btn_invert.clicked.connect(lambda _=False, g=controls: self._invert_lyrics_group(g))
            btn_same.clicked.connect(lambda _=False, g=controls: self._apply_lyrics_preset_same_for_group(g))
            btn_diff.clicked.connect(lambda _=False, g=controls: self._apply_lyrics_preset_diff_for_group(g))
            btn_save.clicked.connect(lambda _=False, g=controls: self._save_lyrics_group(g))
            btn_cancel.clicked.connect(lambda _=False, g=controls: self._cancel_lyrics_group(g))
            self._register_dynamic_button(btn_invert)
            self._register_dynamic_button(btn_same)
            self._register_dynamic_button(btn_diff)
            self._register_dynamic_button(btn_save)
            self._register_dynamic_button(btn_cancel)

            self._apply_default_lyrics_checks(group_rows, row_controls)
            self.lyrics_groups_layout.addWidget(frame)
        self.lyrics_groups_layout.addStretch(1)

    def _build_lyrics_row_widget(self, row: dict) -> dict:
        """\u6784\u5efa\u6b4c\u8bcd\u5ba1\u67e5\u884c\u63a7\u4ef6\uff0c\u542b\u52fe\u9009\u3001\u5efa\u8bae\u4e0e\u7ed1\u5b9a\u5165\u53e3\u3002"""
        payload = dict(row)
        container = QFrame()
        container.setFrameShape(QFrame.Shape.NoFrame)
        container.setStyleSheet("QFrame{background:#ffffff;border:1px solid #d8e2ef;border-radius:6px;}")
        outer = QVBoxLayout(container)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(2)

        top = _ClickableFrame()
        top.setFrameShape(QFrame.Shape.NoFrame)
        top.setStyleSheet("QFrame{background:transparent;border:none;}")
        row_layout = QHBoxLayout(top)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        checkbox = QCheckBox()
        checkbox.setChecked(False)
        checkbox.setStyleSheet("QCheckBox::indicator{width:28px;height:28px;}")
        lbl_file = _ClickableLabel(str(payload.get("lyrics_file", "") or ""))
        lbl_file.setMinimumWidth(260)
        lbl_file.setToolTip(str(payload.get("lyrics_source", "") or ""))
        lbl_file.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lbl_score = _ClickableLabel(f"{_safe_float(payload.get('score', 0.0), 0.0):.4f}")
        lbl_score.setFixedWidth(88)
        lbl_score.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl_score.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lbl_reason = _ClickableLabel(str(payload.get("reason", "")).replace("原因", ""))
        lbl_reason.setToolTip(str(payload.get("review_id", "") or ""))
        lbl_reason.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        row_layout.addWidget(checkbox)
        row_layout.addWidget(lbl_file, 3)
        row_layout.addWidget(lbl_score)
        row_layout.addWidget(lbl_reason, 2)
        outer.addWidget(top)

        link_bind_row = _ClickableFrame()
        link_bind_row.setFrameShape(QFrame.Shape.NoFrame)
        link_bind_row.setStyleSheet("QFrame{background:transparent;border:none;}")
        bind_layout = QHBoxLayout(link_bind_row)
        bind_layout.setContentsMargins(34, 0, 0, 0)
        bind_layout.setSpacing(6)
        bind_icon = QLabel("🔗")
        bind_text = QLabel("点击绑定数据库歌曲")
        bind_text.setStyleSheet("color:#5d6f86;")
        bind_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        bind_text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        bind_layout.addWidget(bind_icon)
        bind_layout.addWidget(bind_text, 1)
        bind_layout.addStretch(1)
        outer.addWidget(link_bind_row)

        suggest_text = str(payload.get("suggest_track", "") or "").strip()
        link_suggest_row: _ClickableFrame | None = None
        if suggest_text:
            link_suggest_row = _ClickableFrame()
            link_suggest_row.setFrameShape(QFrame.Shape.NoFrame)
            link_suggest_row.setStyleSheet("QFrame{background:transparent;border:none;}")
            suggest_layout = QHBoxLayout(link_suggest_row)
            suggest_layout.setContentsMargins(34, 0, 0, 0)
            suggest_layout.setSpacing(6)
            suggest_icon = QLabel("🔗")
            suggest_label = QLabel(f"建议：{suggest_text}")
            suggest_label.setStyleSheet("color:#425b7a;")
            suggest_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            suggest_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            suggest_layout.addWidget(suggest_icon)
            suggest_layout.addWidget(suggest_label, 1)
            suggest_layout.addStretch(1)
            outer.addWidget(link_suggest_row)

        row_ctrl: dict[str, object] = {
            "row": payload,
            "container": container,
            "checkbox": checkbox,
        }

        def _preview() -> None:
            self._on_lyrics_row_clicked(payload)

        def _edit_mapping() -> None:
            self._map_lyrics_row(payload)

        checkbox.clicked.connect(lambda _checked=False: _preview())
        top.clicked.connect(_preview)
        link_bind_row.clicked.connect(_edit_mapping)
        if link_suggest_row is not None:
            link_suggest_row.clicked.connect(_edit_mapping)
        return row_ctrl

    def _on_lyrics_row_clicked(self, row: dict) -> None:
        """\u66f4\u65b0\u6b4c\u8bcd\u53cc\u680f\u9884\u89c8\u961f\u5217\uff08\u6700\u8fd1\u4e24\u4e2a\u6761\u76ee\uff09\u3002"""
        payload = dict(row or {})
        if not payload:
            return
        row_key = "|".join(
            [
                str(payload.get("review_id", "") or ""),
                str(payload.get("lyrics_source", "") or ""),
                str(payload.get("lyrics_id", "") or ""),
            ]
        ).strip("|")
        if self._preview_rows:
            prev = self._preview_rows[-1]
            prev_key = "|".join(
                [
                    str(prev.get("review_id", "") or ""),
                    str(prev.get("lyrics_source", "") or ""),
                    str(prev.get("lyrics_id", "") or ""),
                ]
            ).strip("|")
            if row_key and row_key == prev_key:
                return
        self._preview_rows.append(payload)
        rows = list(self._preview_rows)
        if len(rows) == 1:
            rows = [rows[0], rows[0]]
        self.preview_left.setPlainText(self._read_lyrics_text(rows[-2]))
        self.preview_right.setPlainText(self._read_lyrics_text(rows[-1]))

    @staticmethod

    def _lyrics_line_count(row: dict) -> int:
        """\u63d0\u53d6\u6b4c\u8bcd\u884c\u6570\u5b57\u6bb5\u7528\u4e8e\u6392\u5e8f\u51b3\u7b56\u3002"""
        return _safe_int(row.get("line_count", 0), 0)

    @staticmethod

    def _lyrics_imported_at(row: dict) -> str:
        """\u63d0\u53d6\u6b4c\u8bcd\u5bfc\u5165\u65f6\u95f4\u5b57\u6bb5\u7528\u4e8e\u6392\u5e8f\u51b3\u7b56\u3002"""
        return str(row.get("imported_at", "") or "")

    @staticmethod

    def _lyrics_source_mtime(row: dict) -> float:
        """\u63d0\u53d6\u6b4c\u8bcd\u6587\u4ef6\u4fee\u6539\u65f6\u95f4\u7528\u4e8e\u6392\u5e8f\u51b3\u7b56\u3002"""
        return _safe_float(row.get("source_mtime", 0.0), 0.0)

    @staticmethod

    def _lyrics_group_same_file(rows: list[dict]) -> bool:
        """\u5224\u65ad\u4e00\u7ec4\u6b4c\u8bcd\u662f\u5426\u53ef\u89c6\u4e3a\u540c\u540d\u6587\u4ef6\u96c6\u5408\u3002"""
        keys = {_canonical_lyrics_name(str(r.get("lyrics_file", "") or "")) for r in rows}
        keys.discard("")
        return len(keys) <= 1

    def _apply_default_lyrics_checks(self, rows: list[dict], row_controls: list[dict]) -> None:
        """\u6309\u7b56\u7565\u8bbe\u7f6e\u6b4c\u8bcd\u7ec4\u9ed8\u8ba4\u52fe\u9009\u9879\u3002"""
        if not rows or not row_controls:
            return
        for row_ctrl in row_controls:
            checkbox = row_ctrl.get("checkbox")
            if isinstance(checkbox, QCheckBox):
                checkbox.setChecked(False)

        pairs = list(zip(rows, row_controls))
        if self._lyrics_group_same_file(rows):
            target = max(
                pairs,
                key=lambda p: (
                    self._lyrics_line_count(p[0]),
                    -_lyrics_file_bracket_count(str(p[0].get("lyrics_file", "") or "")),
                    -len(str(p[0].get("lyrics_file", "") or "")),
                    str(p[0].get("lyrics_source", "") or ""),
                ),
            )
        else:
            target = max(
                pairs,
                key=lambda p: (
                    self._lyrics_source_mtime(p[0]),
                    self._lyrics_imported_at(p[0]),
                    self._lyrics_line_count(p[0]),
                    str(p[0].get("lyrics_source", "") or ""),
                ),
            )
        target_ctrl = target[1]
        checkbox = target_ctrl.get("checkbox")
        if isinstance(checkbox, QCheckBox):
            checkbox.setChecked(True)

    def _on_lyrics_item_clicked(self, item: QTreeWidgetItem, _col: int, _tree: QTreeWidget | None = None) -> None:
        """\u5904\u7406\u6b4c\u8bcd\u6811\u70b9\u51fb\u5e76\u8054\u52a8\u9884\u89c8\u3002"""
        row = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if not row or row.get("_footer") or row.get("_meta_row"):
            return
        if row.get("_link_row"):
            self._map_lyrics_row(row)
            return
        row_key = "|".join(
            [
                str(row.get("review_id", "") or ""),
                str(row.get("lyrics_source", "") or ""),
                str(row.get("lyrics_id", "") or ""),
            ]
        ).strip("|")
        if self._preview_rows:
            prev = self._preview_rows[-1]
            prev_key = "|".join(
                [
                    str(prev.get("review_id", "") or ""),
                    str(prev.get("lyrics_source", "") or ""),
                    str(prev.get("lyrics_id", "") or ""),
                ]
            ).strip("|")
            if row_key and row_key == prev_key:
                return
        self._preview_rows.append(dict(row))
        rows = list(self._preview_rows)
        if len(rows) == 1:
            rows = [rows[0], rows[0]]
        self.preview_left.setPlainText(self._read_lyrics_text(rows[-2]))
        self.preview_right.setPlainText(self._read_lyrics_text(rows[-1]))

    def _map_lyrics_row(self, row: dict) -> None:
        """\u6253\u5f00\u6b4c\u66f2\u9009\u62e9\u7a97\u53e3\u5e76\u4fee\u6539\u6b4c\u8bcd\u7ed1\u5b9a\u3002"""
        lyrics_id = str(row.get("lyrics_id", "") or "")
        if not lyrics_id:
            QMessageBox.warning(self, "修改建议歌曲", "当前行没有有效 lyrics_id。")
            return
        # 避免循环依赖：在运行时从主审查模块拿选择对话框。
        from musearc.ui.review_page import _TrackPickerDialog

        picker = _TrackPickerDialog(self, self.facade)
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        self.facade.set_primary_track_for_lyrics(lyrics_id, picker.selected_track_id)
        self.reload_reviews()

    def _read_lyrics_text(self, row: dict) -> str:
        """\u8bfb\u53d6\u6b4c\u8bcd\u6587\u4ef6\u6587\u672c\u7528\u4e8e\u9884\u89c8\u3002"""
        storage_rel = str(row.get("storage_relpath", "") or "")
        if storage_rel:
            target = Path(self.facade.library_root) / storage_rel
            if target.exists():
                try:
                    return target.read_text(encoding="utf-8")
                except Exception as exc:
                    return f"无法读取歌词文件: {exc}"
        preview = str(row.get("preview", "") or "").strip()
        return preview or "（无可用歌词内容）"

    def _sync_preview_scrollbars(self, *, from_left: bool, value: int) -> None:
        """\u540c\u6b65\u5de6\u53f3\u6b4c\u8bcd\u9884\u89c8\u6eda\u52a8\u4f4d\u7f6e\u3002"""
        if self._sync_preview_scroll:
            return
        self._sync_preview_scroll = True
        try:
            if from_left:
                self.preview_right.verticalScrollBar().setValue(value)
            else:
                self.preview_left.verticalScrollBar().setValue(value)
        finally:
            self._sync_preview_scroll = False

    def _apply_lyrics_preset_same_for_group(self, group: dict) -> None:
        """\u5e94\u7528“\u8fd9\u662f\u76f8\u540c\u6b4c\u8bcd”\u9884\u8bbe\u3002"""
        rows: list[dict] = []
        row_controls: list[dict] = []
        entries = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in entries if isinstance(entries, list) else []:
            row = row_ctrl.get("row") if isinstance(row_ctrl, dict) else {}
            if not isinstance(row, dict):
                continue
            rows.append(dict(row))
            row_controls.append(row_ctrl)
        self._apply_default_lyrics_checks(rows, row_controls)

    def _apply_lyrics_preset_diff_for_group(self, group: dict) -> None:
        """\u5e94\u7528“\u8fd9\u662f\u4e0d\u540c\u6b4c\u8bcd”\u9884\u8bbe\u3002"""
        entries = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in entries if isinstance(entries, list) else []:
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            if isinstance(checkbox, QCheckBox):
                checkbox.setChecked(True)

    def _invert_lyrics_group(self, group: dict) -> None:
        """\u5bf9\u5f53\u524d\u6b4c\u8bcd\u5ba1\u67e5\u7ec4\u6267\u884c\u53cd\u9009\u3002"""
        entries = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in entries if isinstance(entries, list) else []:
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            if isinstance(checkbox, QCheckBox):
                checkbox.setChecked(not checkbox.isChecked())

    def _save_lyrics_group(self, group: dict) -> None:
        """\u4fdd\u5b58\u5f53\u524d\u6b4c\u8bcd\u5ba1\u67e5\u7ec4\u52fe\u9009\u7ed3\u679c\u3002"""
        status_by_review: dict[str, bool] = {}
        restore_lyrics_ids: set[str] = set()
        entries = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in entries if isinstance(entries, list) else []:
            row = row_ctrl.get("row") if isinstance(row_ctrl, dict) else {}
            if not isinstance(row, dict):
                continue
            rid = str(row.get("review_id", "") or "")
            if not rid:
                continue
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            checked = bool(checkbox.isChecked()) if isinstance(checkbox, QCheckBox) else False
            status_by_review[rid] = bool(status_by_review.get(rid, False) or checked)
            if checked:
                restore_id = str(row.get("restore_lyrics_id", "") or "")
                if restore_id:
                    restore_lyrics_ids.add(restore_id)
        if not status_by_review:
            return
        if restore_lyrics_ids:
            self.facade.restore_lyrics(sorted(restore_lyrics_ids))
        resolved_ids = [rid for rid, keep in status_by_review.items() if keep]
        ignored_ids = [rid for rid, keep in status_by_review.items() if not keep]
        if resolved_ids:
            self.facade.resolve_reviews(resolved_ids, status="resolved")
        if ignored_ids:
            self.facade.resolve_reviews(ignored_ids, status="ignored")
        self.reload_reviews()
        self.review_changed.emit()

    def _cancel_lyrics_group(self, group: dict) -> None:
        """\u53d6\u6d88\u5bfc\u5165\u5f53\u524d\u6b4c\u8bcd\u5ba1\u67e5\u7ec4\u3002"""
        ids = self._review_ids_for_group(group)
        if not ids:
            return
        self.facade.resolve_reviews(ids, status="ignored")
        self.reload_reviews()
        self.review_changed.emit()
