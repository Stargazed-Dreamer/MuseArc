from __future__ import annotations

"""审查页面-歌曲审查区 Mixin。

该模块仅承载歌曲审查区的大体量 UI 与交互逻辑，避免主页面文件过长。
"""

import subprocess
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


def _safe_float(value, default: float = 0.0) -> float:
    """
    安全地将值转换为浮点数，转换失败时返回默认值。

    参数:
        value: 需要被转换为浮点数的任意类型值。
        default (float, 可选): 当转换失败时返回的默认值，默认为0.0。

    返回值:
        float: 转换成功返回对应的浮点数，否则返回默认值。
    """
    try:
        return float(value)  # 尝试将输入值转换为浮点数
    except Exception:
        return default  # 转换失败时返回预设的默认值


def _safe_int(value, default: int = 0) -> int:
    """将值安全地转换为整数。

    如果值为列表、元组、字典或集合，则直接返回默认值。
    尝试将值转换为整数，如果值为None或假值，则使用0进行转换。
    如果转换失败（例如值为非数字字符串），则返回默认值。

    参数:
        value: 任何要尝试转换为整数的值。
        default: 整数，默认值为0。

    返回:
        int: 转换后的整数或默认值。
    """
    if isinstance(value, (list, tuple, dict, set)):  # 检查值是否为列表、元组、字典或集合，如果是则直接返回默认值
        return default
    try:
        return int(value or 0)  # 尝试将值转换为整数，如果值为假值则使用0作为默认输入
    except Exception:  # 捕获所有转换异常，如值无法转换为整数
        return default


def _format_mmss(seconds: int) -> str:
    """
    将秒数格式化为"MM:SS"格式的字符串。

    参数:
        seconds (int): 秒数。

    返回:
        str: 格式化后的字符串，格式为"MM:SS"。
    """
    sec = max(0, _safe_int(seconds, 0))  # 安全转换为整数并确保非负
    return f"{sec // 60:02d}:{sec % 60:02d}"  # 计算分钟和秒数，并格式化为两位数字符串


def _track_label(track: dict) -> str:
    return f"{track.get('artist', '')} - {track.get('title', '')} ({track.get('track_id', '')})"


def _looks_like_hash_filename(name: str) -> bool:
    """
    检查文件名是否看起来像一个哈希文件名。

    参数：
        name (str): 要检查的文件名。

    返回：
        bool: 如果文件名以"trk_"开头且长度至少12个字符，则返回True；否则返回False。
    """
    text = Path(str(name or "")).stem.casefold()  # 确保name不为None或空，转换为字符串并取文件名部分，然后转为小写以进行不区分大小写的比较
    if text.startswith("trk_") and len(text) >= 12:  # 检查文件名是否以"trk_"前缀开头且总长度至少为12个字符
        return True
    return False


def _format_rank(value: str | None) -> int:
    """
    根据音频格式名称返回一个排名值。
    
    参数：
    value (str | None): 音频格式名称的字符串，可能为None。
    
    返回：
    int: 音频格式的排名值，范围从40到90，根据格式类型而定。
    """
    # 处理输入值：如果为None或空，则用空字符串代替，然后去除空格、转为小写并移除句点
    text = str(value or "").strip().lower().replace(".", "")
    # 定义音频格式到排名值的映射字典
    rank = {
        "flac": 90,
        "wav": 85,
        "ape": 80,
        "alac": 76,
        "m4a": 70,
        "aac": 68,
        "opus": 66,
        "ogg": 62,
        "wma": 56,
        "mp3": 50,
    }
    # 获取对应排名，如果格式不在字典中则返回默认值40
    return rank.get(text, 40)


class _ClickableFrame(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """
        处理鼠标点击事件。

        参数:
        event (QMouseEvent): 鼠标事件对象，包含点击位置和按钮信息。

        返回值:
        无返回值（None）。
        """
        if event.button() == Qt.MouseButton.LeftButton:  # 检查是否左键点击
            cb = getattr(self, '_click_cb', None)  # 从实例属性获取回调函数，若不存在则为None
            if callable(cb):  # 如果回调函数存在且可调用
                cb()  # 执行回调函数
            self.clicked.emit()  # 发出clicked信号，通知外部点击事件
        super().mousePressEvent(event)  # 调用父类的mousePressEvent方法，确保事件正确处理


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            cb = getattr(self, '_click_cb', None)
            if callable(cb):
                cb()
            self.clicked.emit()
        super().mousePressEvent(event)


class ReviewPageSongMixin:
    @staticmethod
    def _aggregate_song_group_rows(rows: list[dict]) -> list[dict]:
        """\u6309\u6e90\u6587\u4ef6\u805a\u5408\u540c\u7ec4\u884c\uff0c\u5e76\u5f52\u5e76\u5019\u9009\u6b4c\u66f2\u4fe1\u606f\u3002"""
        merged: dict[tuple[str, str], dict] = {}
        order: list[tuple[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_path = str(row.get("source_path", "") or "")
            review_id = str(row.get("review_id", "") or "")
            key = (review_id or source_path, source_path)
            if key not in merged:
                base = dict(row)
                base["candidates"] = []
                merged[key] = base
                order.append(key)
            target = merged[key]
            candidate_track_id = str(row.get("candidate_track_id", "") or "")
            candidate_path = str(row.get("candidate_path", "") or "")
            candidate_file = str(row.get("candidate_file_name", "") or "")
            candidate_track = str(row.get("candidate_track", "") or "")
            has_candidate = any([candidate_track_id, candidate_path, candidate_file, candidate_track])
            if has_candidate:
                candidate = {
                    "candidate_track_id": candidate_track_id,
                    "candidate_path": candidate_path,
                    "candidate_file_name": candidate_file,
                    "candidate_track": candidate_track,
                    "candidate_duration_sec": _safe_float(row.get("candidate_duration_sec", 0), 0),
                    "score": _safe_float(row.get("score", 0), 0.0),
                    "candidate_meta": dict(row.get("candidate_meta") or {}),
                }
                exists = False
                for existing in target["candidates"]:
                    if (
                        str(existing.get("candidate_track_id", "") or "") == candidate_track_id
                        and str(existing.get("candidate_path", "") or "") == candidate_path
                    ):
                        exists = True
                        break
                if not exists:
                    target["candidates"].append(candidate)

        out: list[dict] = []
        for key in order:
            row = merged[key]
            candidates = row.get("candidates") if isinstance(row.get("candidates"), list) else []
            candidates.sort(key=lambda c: _safe_float(c.get("score", 0), 0.0), reverse=True)
            if candidates:
                best = candidates[0]
                row["candidate_track_id"] = str(best.get("candidate_track_id", "") or "")
                row["candidate_path"] = str(best.get("candidate_path", "") or "")
                row["candidate_file_name"] = str(best.get("candidate_file_name", "") or "")
                row["candidate_track"] = str(best.get("candidate_track", "") or "")
                row["candidate_duration_sec"] = _safe_float(best.get("candidate_duration_sec", 0), 0)
                row["candidate_meta"] = dict(best.get("candidate_meta") or {})
                row["score"] = _safe_float(best.get("score", row.get("score", 0)), 0.0)
            else:
                row["candidate_track_id"] = ""
                row["candidate_path"] = ""
                row["candidate_file_name"] = ""
                row["candidate_track"] = ""
                row["candidate_duration_sec"] = 0.0
                row["candidate_meta"] = {}
            out.append(row)
        return out

    @staticmethod

    def _song_group_queue_paths(group_rows: list[dict]) -> list[str]:
        """\u751f\u6210\u4e00\u4e2a\u5ba1\u67e5\u7ec4\u7684\u64ad\u653e\u961f\u5217\u8def\u5f84\uff08\u53bb\u91cd\u540e\uff09\u3002"""
        paths: list[str] = []
        seen: set[str] = set()
        for row in group_rows:
            if not isinstance(row, dict):
                continue
            for key in ("source_path", "candidate_path"):
                text = str(row.get(key, "") or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    paths.append(text)
            candidates = row.get("candidates") if isinstance(row.get("candidates"), list) else []
            for cand in candidates:
                if not isinstance(cand, dict):
                    continue
                text = str(cand.get("candidate_path", "") or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    paths.append(text)
        return paths

    @staticmethod
    def _open_in_file_manager(path_text: str) -> None:
        """功能：在文件管理器中打开指定路径或选择指定文件。
    参数：path_text (str) - 要打开的路径或文件路径。
    返回值：None - 无返回值。
    """
        # 将path_text转换为字符串，如果为None则使用空字符串，并去除首尾空白
        target_text = str(path_text or "").strip()
        # 如果路径文本为空，则直接返回
        if not target_text:
            return
        try:
            # 创建Path对象
            target = Path(target_text)
            # 如果路径存在且是文件，则打开文件管理器并选择该文件
            if target.exists() and target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            # 如果路径存在（可能是目录），则打开该目录
            elif target.exists():
                subprocess.Popen(["explorer", str(target)])
            # 如果路径不存在但父目录存在，则打开父目录
            elif target.parent.exists():
                subprocess.Popen(["explorer", str(target.parent)])
        # 捕获所有异常，防止程序崩溃
        except Exception:
            return

    def _fill_song_tree(self, rows: list[dict]) -> None:
        """\u6784\u5efa\u6b4c\u66f2\u5ba1\u67e5\u5206\u7ec4\u754c\u9762\uff08\u6bcf\u7ec4\u72ec\u7acb frame\uff09\u3002"""
        self._song_group_controls.clear()
        self._clear_group_layout(self.song_groups_layout)
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            group_title = str(row.get("group_title", "") or row.get("group_key", "") or "未分组")
            groups[group_title].append(row)

        if not groups:
            empty = QLabel("暂无歌曲待审查")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.song_groups_layout.addWidget(empty)
            self.song_groups_layout.addStretch(1)
            return

        for group_key in sorted(groups.keys(), key=lambda s: s.casefold()):
            group_rows = self._aggregate_song_group_rows(list(groups[group_key]))
            group_queue_paths = self._song_group_queue_paths(group_rows)
            seen_candidate_keys: set[tuple[str, str]] = set()
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
                ("播放", 0, 44),
                ("位置", 0, 44),
                ("文件名", 3, 0),
                ("来源", 0, 84),
                ("相对相似度", 0, 96),
                ("审查原因", 2, 0),
            ]:
                lbl = QLabel(text)
                font = lbl.font()
                font.setBold(True)
                lbl.setFont(font)
                if fixed > 0:
                    lbl.setFixedWidth(fixed)
                header_layout.addWidget(lbl, stretch)
            host.addWidget(header)

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 300)
            row_controls: list[dict] = []

            max_dur = 300
            for row in group_rows:
                row_ctrl = self._build_song_row_widget(row, slider, group_queue_paths, seen_candidate_keys)
                row_controls.append(row_ctrl)
                host.addWidget(row_ctrl["container"])
                max_dur = max(max_dur, _safe_int(row.get("candidate_duration_sec", 0), 0))
                candidates = row.get("candidates") if isinstance(row.get("candidates"), list) else []
                for candidate in candidates:
                    max_dur = max(max_dur, _safe_int(candidate.get("candidate_duration_sec", 0), 0))

            row_ops_host = QWidget()
            row_ops = QHBoxLayout(row_ops_host)
            row_ops.setContentsMargins(0, 2, 0, 0)
            row_ops.setSpacing(8)
            btn_invert = QPushButton("反选")
            btn_same = QPushButton("这是相同歌曲")
            btn_diff = QPushButton("这是不同歌曲")
            btn_save = QPushButton("保存勾选的文件")
            btn_cancel = QPushButton("取消导入")
            slider.setRange(0, max_dur)
            label_time = QLabel("00:00")
            row_ops.addWidget(btn_invert)
            row_ops.addWidget(btn_same)
            row_ops.addWidget(btn_diff)
            row_ops.addWidget(btn_save)
            row_ops.addWidget(btn_cancel)
            row_ops.addWidget(QLabel("组进度"))
            row_ops.addWidget(slider, 1)
            row_ops.addWidget(label_time)
            row_ops.addStretch(1)
            host.addWidget(row_ops_host)

            controls = {"group_key": group_key, "rows": row_controls, "slider": slider}
            self._song_group_controls[group_key] = controls
            slider.valueChanged.connect(lambda value, lbl=label_time: lbl.setText(_format_mmss(value)))
            btn_invert.clicked.connect(lambda _=False, g=controls: self._invert_song_group(g))
            btn_same.clicked.connect(lambda _=False, g=controls: self._apply_song_preset_same_for_group(g))
            btn_diff.clicked.connect(lambda _=False, g=controls: self._apply_song_preset_diff_for_group(g))
            btn_save.clicked.connect(lambda _=False, g=controls: self._save_song_group(g))
            btn_cancel.clicked.connect(lambda _=False, g=controls: self._cancel_song_group(g))
            self._register_dynamic_button(btn_invert)
            self._register_dynamic_button(btn_same)
            self._register_dynamic_button(btn_diff)
            self._register_dynamic_button(btn_save)
            self._register_dynamic_button(btn_cancel)

            self.song_groups_layout.addWidget(frame)
            self._apply_song_preset_same_for_group(controls)
        self.song_groups_layout.addStretch(1)

    def _build_song_row_widget(
        self,
        row: dict,
        slider: QSlider,
        group_queue_paths: list[str],
        seen_candidate_keys: set[tuple[str, str]],
    ) -> dict:
        """\u6784\u5efa\u6b4c\u66f2\u5ba1\u67e5\u884c\u63a7\u4ef6\uff0c\u542b\u52fe\u9009\u3001\u64ad\u653e\u3001\u5143\u6570\u636e\u7f16\u8f91\u533a\u3002"""
        payload = dict(row)
        candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
        if not candidates:
            candidate_track_id = str(payload.get("candidate_track_id", "") or "")
            candidate_file = str(payload.get("candidate_file_name", "") or "")
            candidate_path = str(payload.get("candidate_path", "") or "")
            candidate_track = str(payload.get("candidate_track", "") or "")
            if any([candidate_track_id, candidate_file, candidate_path, candidate_track]):
                candidates = [
                    {
                        "candidate_track_id": candidate_track_id,
                        "candidate_file_name": candidate_file,
                        "candidate_path": candidate_path,
                        "candidate_track": candidate_track,
                        "candidate_duration_sec": _safe_float(payload.get("candidate_duration_sec", 0), 0),
                        "score": _safe_float(payload.get("score", 0), 0.0),
                    }
                ]
        payload["candidates"] = candidates
        container = QFrame()
        container.setFrameShape(QFrame.Shape.NoFrame)
        container.setStyleSheet("QFrame{background:#ffffff;border:1px solid #d8e2ef;border-radius:6px;}")
        outer = QVBoxLayout(container)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(3)

        top = _ClickableFrame()
        top.setFrameShape(QFrame.Shape.NoFrame)
        top.setStyleSheet("QFrame{background:transparent;border:none;}")
        row_layout = QHBoxLayout(top)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        checkbox = QCheckBox()
        checkbox.setChecked(True)
        checkbox.setStyleSheet("QCheckBox::indicator{width:28px;height:28px;}")

        btn_play = QPushButton("▶")
        btn_play.setFixedWidth(34)
        btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reveal = QPushButton("📁")
        btn_reveal.setFixedWidth(34)
        btn_reveal.setCursor(Qt.CursorShape.PointingHandCursor)

        source_path = str(payload.get("source_path", "") or "").strip()
        source_file = str(payload.get("source_file", "") or "").strip() or Path(source_path).name
        lbl_file_name = _ClickableLabel(source_file)
        lbl_file_name.setMinimumWidth(180)
        lbl_file_name.setToolTip(source_path)

        lbl_source_kind = QLabel("待导入")
        lbl_source_kind.setFixedWidth(84)
        lbl_source_kind.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_source_kind.setStyleSheet("color:#2f7dff;")

        lbl_score = _ClickableLabel(f"{_safe_float(payload.get('score', 0.0), 0.0):.4f}")
        lbl_score.setFixedWidth(96)
        lbl_score.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        lbl_reason = _ClickableLabel(str(payload.get("reason", "")).replace("原因", ""))
        lbl_reason.setToolTip(str(payload.get("review_id", "") or ""))

        row_layout.addWidget(checkbox)
        row_layout.addWidget(btn_play)
        row_layout.addWidget(btn_reveal)
        row_layout.addWidget(lbl_file_name, 3)
        row_layout.addWidget(lbl_source_kind)
        row_layout.addWidget(lbl_score)
        outer.addWidget(top)

        reason_row = QWidget()
        reason_layout = QHBoxLayout(reason_row)
        reason_layout.setContentsMargins(106, 0, 0, 0)
        reason_layout.setSpacing(6)
        reason_layout.addWidget(lbl_reason, 1)
        outer.addWidget(reason_row)

        row_ctrl: dict[str, object] = {
            "row": payload,
            "container": container,
            "checkbox": checkbox,
            "source_checkbox": checkbox,
            "score": _safe_float(payload.get("score", 0), 0.0),
            "candidate_controls": [],
        }

        btn_play.clicked.connect(
            lambda _=False, r=payload, s=slider: self._play_with_external_player(
                str(r.get("source_path", "")).strip(),
                s.value(),
                queue_paths=list(group_queue_paths),
            )
        )
        btn_reveal.clicked.connect(lambda _=False, p=source_path: self._open_in_file_manager(p))
        for candidate in candidates:
            candidate_track_id = str(candidate.get("candidate_track_id", "") or "").strip()
            candidate_path = str(candidate.get("candidate_path", "") or "").strip()
            candidate_key = (candidate_track_id, "") if candidate_track_id else ("", candidate_path)
            if candidate_key in seen_candidate_keys:
                continue
            if candidate_track_id or candidate_path:
                seen_candidate_keys.add(candidate_key)
            candidate_row = _ClickableFrame()
            candidate_row.setFrameShape(QFrame.Shape.NoFrame)
            candidate_row.setStyleSheet("QFrame{background:transparent;border:none;}")
            candidate_layout = QHBoxLayout(candidate_row)
            candidate_layout.setContentsMargins(0, 0, 0, 0)
            candidate_layout.setSpacing(8)

            candidate_checkbox = QCheckBox()
            candidate_checkbox.setChecked(False)
            candidate_checkbox.setStyleSheet("QCheckBox::indicator{width:28px;height:28px;}")
            btn_play_candidate = QPushButton("▶")
            btn_play_candidate.setFixedWidth(34)
            btn_play_candidate.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_reveal_candidate = QPushButton("📁")
            btn_reveal_candidate.setFixedWidth(34)
            btn_reveal_candidate.setCursor(Qt.CursorShape.PointingHandCursor)

            candidate_file = str(candidate.get("candidate_file_name", "") or "").strip()
            candidate_track = str(candidate.get("candidate_track", "") or "").strip()
            if not candidate_file and candidate_path:
                candidate_file = Path(candidate_path).name
            candidate_text = candidate_track if _looks_like_hash_filename(candidate_file) else (candidate_file or candidate_track or "（无候选）")
            lbl_candidate_name = _ClickableLabel(candidate_text)
            lbl_candidate_name.setMinimumWidth(180)
            candidate_tip = str(candidate.get("candidate_path", "") or "")
            if candidate_track:
                candidate_tip = f"{candidate_tip}\n{candidate_track}" if candidate_tip else candidate_track
            lbl_candidate_name.setToolTip(candidate_tip)

            lbl_candidate_source = QLabel("库")
            lbl_candidate_source.setFixedWidth(84)
            lbl_candidate_source.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_candidate_source.setStyleSheet("color:#4f5f72;")
            lbl_candidate_score = QLabel("1.0000")
            lbl_candidate_score.setFixedWidth(96)
            lbl_candidate_score.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl_candidate_reason = _ClickableLabel(str(payload.get("reason", "")).replace("原因", ""))
            lbl_candidate_reason.setStyleSheet("color:#5d6f86;")

            candidate_layout.addWidget(candidate_checkbox)
            candidate_layout.addWidget(btn_play_candidate)
            candidate_layout.addWidget(btn_reveal_candidate)
            candidate_layout.addWidget(lbl_candidate_name, 3)
            candidate_layout.addWidget(lbl_candidate_source)
            candidate_layout.addWidget(lbl_candidate_score)
            outer.addWidget(candidate_row)
            candidate_reason_row = QWidget()
            candidate_reason_layout = QHBoxLayout(candidate_reason_row)
            candidate_reason_layout.setContentsMargins(106, 0, 0, 0)
            candidate_reason_layout.setSpacing(6)
            candidate_reason_layout.addWidget(lbl_candidate_reason, 1)
            outer.addWidget(candidate_reason_row)
            row_ctrl["candidate_controls"].append(
                {
                    "checkbox": candidate_checkbox,
                    "track_id": str(candidate.get("candidate_track_id", "") or ""),
                    "quality_score": _safe_float(
                        (
                            candidate.get("candidate_meta", {}).get("quality_score")
                            if isinstance(candidate.get("candidate_meta"), dict)
                            else candidate.get("quality_score", 0)
                        ),
                        0.0,
                    ),
                    "format_rank": _format_rank(
                        (
                            candidate.get("candidate_meta", {}).get("storage_format")
                            if isinstance(candidate.get("candidate_meta"), dict)
                            else ""
                        )
                        or (
                            candidate.get("candidate_meta", {}).get("source_ext")
                            if isinstance(candidate.get("candidate_meta"), dict)
                            else ""
                        )
                        or Path(str(candidate.get("candidate_file_name", "") or "")).suffix
                    ),
                }
            )

            btn_play_candidate.clicked.connect(
                lambda _=False, c=dict(candidate), s=slider: self._play_with_external_player(
                    str(c.get("candidate_path", "")).strip(),
                    s.value(),
                    queue_paths=list(group_queue_paths),
                )
            )
            btn_reveal_candidate.clicked.connect(
                lambda _=False, p=str(candidate.get("candidate_path", "")).strip(): self._open_in_file_manager(p)
            )
        return row_ctrl

    def _toggle_song_meta_panel(self, row_ctrl: dict) -> None:
        """\u5c55\u5f00\u6216\u6536\u8d77\u6b4c\u66f2\u884c\u7684\u5019\u9009\u5143\u6570\u636e\u7f16\u8f91\u9762\u677f\u3002"""
        panel = row_ctrl.get("meta_panel")
        if not isinstance(panel, QWidget):
            return
        panel.setVisible(not panel.isVisible())

    def _refresh_song_row_candidate_label(self, row_ctrl: dict) -> None:
        """\u5237\u65b0\u884c\u5185\u5019\u9009\u6b4c\u66f2\u6587\u672c\u663e\u793a\u3002"""
        row = row_ctrl.get("row")
        if not isinstance(row, dict):
            return
        track_id = str(row_ctrl.get("candidate_track_id", "") or row.get("candidate_track_id", "") or "")
        meta = row.get("candidate_meta") if isinstance(row.get("candidate_meta"), dict) else {}
        title = str(meta.get("title", "") or "").strip()
        artist = str(meta.get("artist", "") or "").strip()
        if track_id:
            detail = f"{artist or 'Unknown Artist'} - {title or 'Unknown Title'} ({track_id})"
        else:
            detail = str(row.get("candidate_track", "") or "")
        row["candidate_track"] = detail
        name_widget = row_ctrl.get("candidate_name_label")
        detail_widget = row_ctrl.get("candidate_detail_label")
        if isinstance(name_widget, QLabel):
            current_file = str(row.get("candidate_file_name", "") or "").strip()
            name_widget.setText(current_file or detail)
        if isinstance(detail_widget, QLabel):
            detail_widget.setText(detail)

    def _commit_song_meta_edit(self, row_ctrl: dict, field_key: str, value) -> None:
        """\u63d0\u4ea4\u5019\u9009\u5143\u6570\u636e\u7f16\u8f91\u5e76\u5199\u5165\u884c\u7f13\u5b58\u3002"""
        track_id = str(row_ctrl.get("candidate_track_id", "") or "")
        if not track_id:
            return
        cache = row_ctrl.get("meta_cache")
        if not isinstance(cache, dict):
            cache = {}
            row_ctrl["meta_cache"] = cache
        old_value = cache.get(field_key)
        if field_key == "preference_level":
            new_value = max(1, min(10, _safe_int(value, 5)))
            old_comp = max(1, min(10, _safe_int(old_value, 5)))
            if new_value == old_comp:
                return
            payload_value = int(new_value)
        else:
            new_value = str(value or "").strip()
            old_comp = str(old_value or "").strip()
            if new_value == old_comp:
                return
            payload_value = new_value
        try:
            self.facade.update_tracks_fields([track_id], {field_key: payload_value})
        except Exception as exc:
            QMessageBox.warning(self, "编辑候选元数据", f"保存失败: {exc}")
            editor_map = row_ctrl.get("meta_widgets")
            editor = editor_map.get(field_key) if isinstance(editor_map, dict) else None
            if isinstance(editor, QLineEdit):
                editor.setText(str(old_value or ""))
            elif isinstance(editor, QSpinBox):
                editor.setValue(max(1, min(10, _safe_int(old_value, 5))))
            return

        cache[field_key] = payload_value
        row = row_ctrl.get("row")
        if isinstance(row, dict):
            meta = row.get("candidate_meta")
            if not isinstance(meta, dict):
                meta = {}
                row["candidate_meta"] = meta
            meta[field_key] = payload_value
        if field_key in {"title", "artist"}:
            self._refresh_song_row_candidate_label(row_ctrl)
        self.review_changed.emit()

    def _play_with_external_player(self, path_text: str, start_sec: int = 0, *, queue_paths: list[str] | None = None) -> None:
        """\u6309\u8bbe\u7f6e\u8c03\u7528\u5916\u90e8\u64ad\u653e\u5668\uff0c\u652f\u6301\u8d77\u64ad\u4f4d\u7f6e\u4e0e\u961f\u5217\u900f\u4f20\u3002"""
        target = str(path_text or "").strip()
        if not target:
            QMessageBox.information(self, "播放", "当前行没有可播放路径。")
            return
        cfg = self.facade.get_runtime_config()
        mode = str(cfg.ui.player_mode or "external")
        if mode == "builtin":
            top = self.window()
            handler = getattr(top, "queue_and_play_paths", None)
            if not callable(handler):
                QMessageBox.information(self, "播放", "当前窗口不支持内置播放器。")
                return
            queue = list(queue_paths or [])
            if not queue:
                queue = [target]
            if not bool(handler(queue, start_path=target, start_sec=start_sec)):
                QMessageBox.information(self, "播放", "未找到可播放文件。")
            return
        exe = str(cfg.ui.external_player_path or "").strip()
        if not exe:
            QMessageBox.warning(self, "播放", "请先在设置中配置外部播放器可执行文件路径。")
            return
        cmd = [exe, target]
        start = max(0, int(start_sec))
        low_name = Path(exe).name.casefold()
        if start > 0:
            if "ffplay" in low_name:
                cmd = [exe, "-ss", str(start), target]
            elif "vlc" in low_name:
                cmd = [exe, f"--start-time={start}", target]
            elif "mpv" in low_name:
                cmd = [exe, f"--start={start}", target]
        try:
            subprocess.Popen(cmd)
        except Exception as exc:
            QMessageBox.critical(self, "播放失败", str(exc))

    def _song_controls_for_tree(self, tree: QTreeWidget) -> dict | None:
        """\u6839\u636e\u6811\u63a7\u4ef6\u53cd\u67e5\u6240\u5c5e\u6b4c\u66f2\u5ba1\u67e5\u7ec4\u63a7\u5236\u5bf9\u8c61\u3002"""
        for controls in self._song_group_controls.values():
            if controls.get("tree") is tree:
                return controls
        return None

    def _on_song_item_clicked(self, item: QTreeWidgetItem, col: int, tree: QTreeWidget | None = None) -> None:
        """\u5904\u7406\u6b4c\u66f2\u5ba1\u67e5\u6811\u5355\u51fb\uff1a\u52fe\u9009\u3001\u64ad\u653e\u6216\u5c55\u5f00\u7f16\u8f91\u3002"""
        row = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if not row or row.get("_footer"):
            return
        if row.get("_meta_row"):
            self._edit_song_meta_row(item, row)
            return
        if col == 1:
            start = 0
            if tree is None:
                tree = item.treeWidget()
            if isinstance(tree, QTreeWidget):
                controls = self._song_controls_for_tree(tree) or {}
                slider = controls.get("slider")
                if isinstance(slider, QSlider):
                    start = slider.value()
            path = str(row.get("source_path", "") or row.get("candidate_path", "")).strip()
            self._play_with_external_player(path, start)
            return
        self._toggle_song_meta(item)

    def _toggle_song_meta(self, item: QTreeWidgetItem) -> None:
        """\u5207\u6362\u5019\u9009\u5143\u6570\u636e\u660e\u7ec6\u884c\u663e\u793a\u72b6\u6001\u3002"""
        meta_items = self._iter_meta_children(item)
        if not meta_items:
            return
        show = bool(meta_items[0].isHidden())
        for meta_item in meta_items:
            meta_item.setHidden(not show)
        item.setExpanded(show)

    def _edit_song_meta_row(self, item: QTreeWidgetItem, row: dict) -> None:
        """\u901a\u8fc7\u5f39\u7a97\u7f16\u8f91\u5019\u9009\u5143\u6570\u636e\u5b57\u6bb5\u3002"""
        track_id = str(row.get("track_id", "") or "")
        field_key = str(row.get("field_key", "") or "")
        field_label = str(row.get("field_label", "") or field_key)
        if not track_id or not field_key:
            return

        old_value = str(item.text(3) or "")
        if field_key == "preference_level":
            try:
                start = max(1, min(10, int(old_value or "5")))
            except Exception:
                start = 5
            value, ok = QInputDialog.getInt(self, "编辑候选元数据", field_label, value=start, minValue=1, maxValue=10)
            if not ok:
                return
            new_value = int(value)
            display = str(new_value)
        else:
            value, ok = QInputDialog.getText(self, "编辑候选元数据", field_label, text=old_value)
            if not ok:
                return
            new_value = str(value).strip()
            display = new_value

        if str(old_value) == str(display):
            return
        self.facade.update_tracks_fields([track_id], {field_key: new_value})
        item.setText(3, display)
        self.reload_reviews(force_refresh_refs=True)

    def _on_song_item_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        """\u53cc\u51fb\u6b4c\u66f2\u5ba1\u67e5\u884c\u65f6\u8fdb\u5165\u5019\u9009\u6b4c\u66f2\u7f16\u8f91\u3002"""
        row = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if not row or row.get("_meta_row") or row.get("_footer"):
            return
        self._edit_song_candidate_from_row(row)

    def _edit_song_candidate_from_row(self, row: dict) -> None:
        """\u5f39\u51fa\u6b4c\u66f2\u9009\u62e9\u7a97\u53e3\u5e76\u56de\u5199\u5f53\u524d\u5ba1\u67e5\u884c\u5019\u9009\u4fe1\u606f\u3002"""
        track_id = str(row.get("candidate_track_id", "") or "")
        if not track_id:
            return
        track = self._track_map.get(track_id) or {}
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑候选歌曲信息")
        form = QFormLayout(dialog)
        input_title = QLineEdit(str(track.get("title", "") or ""))
        input_artist = QLineEdit(str(track.get("artist", "") or ""))
        input_album = QLineEdit(str(track.get("album", "") or ""))
        form.addRow("标题", input_title)
        form.addRow("艺术家", input_artist)
        form.addRow("专辑", input_album)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        form.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.facade.update_tracks_fields(
            [track_id],
            {
                "title": input_title.text().strip(),
                "artist": input_artist.text().strip(),
                "album": input_album.text().strip(),
            },
        )
        self.reload_reviews(force_refresh_refs=True)

    def _apply_song_preset_same_for_group(self, group: dict) -> None:
        """\u5e94\u7528“\u8fd9\u662f\u76f8\u540c\u6b4c\u66f2”\u9884\u8bbe\uff1a\u9ed8\u8ba4\u4ec5\u4fdd\u7559\u8d28\u91cf\u6700\u4f73\u9879\u3002"""
        best_source: dict | None = None
        best_source_key: tuple[int, int] = (-1, -1)
        best_candidate: tuple[dict, dict] | None = None
        best_candidate_key: tuple[int, int] = (-1, -1)
        rows = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in rows if isinstance(rows, list) else []:
            row = row_ctrl.get("row") if isinstance(row_ctrl, dict) else {}
            if not isinstance(row, dict):
                continue
            source_ext = Path(str(row.get("source_file", "") or row.get("source_path", "") or "")).suffix
            source_rank = _format_rank(source_ext)
            source_quality = int(round(_safe_float(row.get("source_quality_score", 0.0), 0.0) * 1000.0))
            source_key = (source_rank, source_quality)
            if source_key > best_source_key:
                best_source_key = source_key
                best_source = row_ctrl
            candidate_controls = row_ctrl.get("candidate_controls") if isinstance(row_ctrl, dict) else []
            for candidate in candidate_controls if isinstance(candidate_controls, list) else []:
                if not isinstance(candidate, dict):
                    continue
                cand_rank = _safe_int(candidate.get("format_rank", 0), 0)
                cand_quality = int(round(_safe_float(candidate.get("quality_score", 0.0), 0.0) * 1000.0))
                cand_key = (cand_rank, cand_quality)
                if cand_key > best_candidate_key:
                    best_candidate_key = cand_key
                    best_candidate = (row_ctrl, candidate)
        choose_candidate = best_candidate is not None and best_candidate_key > best_source_key
        for row_ctrl in rows if isinstance(rows, list) else []:
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            if isinstance(checkbox, QCheckBox):
                checkbox.setChecked(bool(not choose_candidate and best_source is row_ctrl))
            candidate_controls = row_ctrl.get("candidate_controls") if isinstance(row_ctrl, dict) else []
            for candidate in candidate_controls if isinstance(candidate_controls, list) else []:
                candidate_checkbox = candidate.get("checkbox") if isinstance(candidate, dict) else None
                if isinstance(candidate_checkbox, QCheckBox):
                    candidate_checkbox.setChecked(
                        bool(
                            choose_candidate
                            and best_candidate
                            and best_candidate[0] is row_ctrl
                            and best_candidate[1] is candidate
                        )
                    )

    def _invert_song_group(self, group: dict) -> None:
        """\u5bf9\u5f53\u524d\u6b4c\u66f2\u5ba1\u67e5\u7ec4\u6267\u884c\u53cd\u9009\u3002"""
        rows = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in rows if isinstance(rows, list) else []:
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            if isinstance(checkbox, QCheckBox):
                checkbox.setChecked(not checkbox.isChecked())
            candidate_controls = row_ctrl.get("candidate_controls") if isinstance(row_ctrl, dict) else []
            for candidate in candidate_controls if isinstance(candidate_controls, list) else []:
                candidate_checkbox = candidate.get("checkbox") if isinstance(candidate, dict) else None
                if isinstance(candidate_checkbox, QCheckBox):
                    candidate_checkbox.setChecked(not candidate_checkbox.isChecked())

    def _apply_song_preset_diff_for_group(self, group: dict) -> None:
        """\u5e94\u7528“\u8fd9\u662f\u4e0d\u540c\u6b4c\u66f2”\u9884\u8bbe\uff1a\u9ed8\u8ba4\u5168\u9009\u3002"""
        rows = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in rows if isinstance(rows, list) else []:
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            if isinstance(checkbox, QCheckBox):
                checkbox.setChecked(True)
            candidate_controls = row_ctrl.get("candidate_controls") if isinstance(row_ctrl, dict) else []
            for candidate in candidate_controls if isinstance(candidate_controls, list) else []:
                candidate_checkbox = candidate.get("checkbox") if isinstance(candidate, dict) else None
                if isinstance(candidate_checkbox, QCheckBox):
                    candidate_checkbox.setChecked(True)

    def _save_song_group(self, group: dict) -> None:
        """\u4fdd\u5b58\u5f53\u524d\u6b4c\u66f2\u5ba1\u67e5\u7ec4\u52fe\u9009\u7ed3\u679c\u5e76\u63d0\u4ea4\u5230\u540e\u7aef\u3002"""
        status_by_review: dict[str, bool] = {}
        restore_track_ids: set[str] = set()
        deferred_rows: dict[str, dict] = {}
        rows = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in rows if isinstance(rows, list) else []:
            row = row_ctrl.get("row") if isinstance(row_ctrl, dict) else {}
            if not isinstance(row, dict):
                continue
            rid = str(row.get("review_id", "") or "")
            if not rid:
                continue
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            keep_source = bool(checkbox.isChecked()) if isinstance(checkbox, QCheckBox) else False
            keep_library = False
            selected_library_track_ids: list[str] = []
            candidate_controls = row_ctrl.get("candidate_controls") if isinstance(row_ctrl, dict) else []
            for candidate in candidate_controls if isinstance(candidate_controls, list) else []:
                if not isinstance(candidate, dict):
                    continue
                candidate_checkbox = candidate.get("checkbox")
                if not isinstance(candidate_checkbox, QCheckBox):
                    continue
                if not candidate_checkbox.isChecked():
                    continue
                tid = str(candidate.get("track_id", "") or "")
                if tid:
                    keep_library = True
                    selected_library_track_ids.append(tid)

            status_by_review[rid] = bool(status_by_review.get(rid, False) or keep_source)
            if keep_source:
                restore_id = str(row.get("restore_track_id", "") or "")
                if restore_id:
                    restore_track_ids.add(restore_id)
                if bool(row.get("deferred_import", False)):
                    payload = dict(row)
                    payload["_keep_library"] = keep_library
                    payload["_selected_library_track_ids"] = selected_library_track_ids
                    deferred_rows[rid] = payload
        if not status_by_review:
            return
        if restore_track_ids:
            self.facade.restore_tracks(sorted(restore_track_ids))

        failed_imports: list[tuple[str, str]] = []
        for rid, row in deferred_rows.items():
            source_path = str(row.get("source_path", "") or "").strip()
            existing_track_id = str(row.get("candidate_track_id", "") or "").strip() or None
            keep_library = bool(row.get("_keep_library", False))
            if not source_path:
                failed_imports.append((rid, "缺少源路径"))
                continue
            try:
                result = self.facade.import_track_from_review(
                    source_path,
                    existing_track_id=existing_track_id,
                    replace_existing=not keep_library,
                )
            except Exception as exc:
                failed_imports.append((rid, str(exc)))
                continue
            if str(result.get("status", "")) != "imported":
                failed_imports.append((rid, str(result)))
                continue
        failed_ids = {rid for rid, _ in failed_imports}
        resolved_ids = [rid for rid, keep in status_by_review.items() if keep]
        ignored_ids = [rid for rid, keep in status_by_review.items() if not keep]
        resolved_ids = [rid for rid in resolved_ids if rid not in failed_ids]
        if resolved_ids:
            self.facade.resolve_reviews(resolved_ids, status="resolved")
        if ignored_ids:
            self.facade.resolve_reviews(ignored_ids, status="ignored")
        if failed_imports:
            preview = "\n".join(f"{rid}: {reason}" for rid, reason in failed_imports[:8])
            QMessageBox.warning(
                self,
                "审查导入",
                f"有 {len(failed_imports)} 项导入失败，已保留为待审查。\n{preview}",
            )
        self.reload_reviews(force_refresh_refs=True)
        self.review_changed.emit()

    def _cancel_song_group(self, group: dict) -> None:
        """\u53d6\u6d88\u5bfc\u5165\u5f53\u524d\u6b4c\u66f2\u5ba1\u67e5\u7ec4\u3002"""
        ids = self._review_ids_for_group(group)
        if not ids:
            return
        self.facade.resolve_reviews(ids, status="ignored")
        self.reload_reviews()
        self.review_changed.emit()
