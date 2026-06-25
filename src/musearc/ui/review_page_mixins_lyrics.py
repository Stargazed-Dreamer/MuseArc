from __future__ import annotations

"""审查页面-歌词审查区 Mixin。

该模块承载歌词审查分组、对比预览、绑定歌曲等逻辑，降低主页面复杂度。
"""

import re
import subprocess
from collections import defaultdict, deque
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

def _safe_float(value, default: float = 0.0) -> float:
    """将输入值安全地转换为浮点数。

    尝试将给定的值转换为浮点类型，如果转换过程中发生任何异常，则返回指定的默认值。
    这是一种防御性编程方式，用于处理可能不可靠或格式未知的输入数据。

    参数:
        value: 待转换的值，可以是任何类型。
        default (float, 可选): 当转换失败时返回的默认值，默认为 0.0。

    返回:
        float: 转换成功后的浮点数值，或转换失败时的默认值。
    """
    try:
        return float(value)  # 尝试将输入值转换为浮点数
    except Exception:
        return default  # 如果发生任何异常（如ValueError, TypeError等），则安全地返回默认值


def _safe_int(value, default: int = 0) -> int:
    """安全地将一个值转换为整数，如果转换失败则返回给定的默认值。

    该函数首先检查传入的值是否为不可直接转换为整数的容器类型（如列表、字典等），
    如果是则立即返回默认值。随后尝试使用int()进行转换，若发生任何异常，
    也会返回默认值，从而避免程序因类型转换错误而中断。

    Args:
        value: 需要转换的值。可以是数字、字符串或其他可被int()转换的对象。
        default (int, optional): 转换失败时返回的默认值，默认为0。

    Returns:
        int: 转换成功后的整数，或转换失败时返回的默认值。
    """
    # 排除常见无法直接用int()转换的容器类型
    if isinstance(value, (list, tuple, dict, set)):
        return default
    try:
        # 尝试将值转换为整数；如果值为None或空字符串等“假值”，则用0替代
        return int(value or 0)
    except Exception:
        # 捕获所有可能的转换异常（如ValueError， TypeError等），返回默认值
        return default


def _canonical_lyrics_name(file_name: str) -> str:
    """
    规范化歌词名称函数。

    功能：清理并规范化文件名，使其适合用作歌词名称。
    参数：
        file_name (str): 输入的文件名字符串。
    返回值：
        str: 处理后的规范化字符串。
    """
    # 使用Path提取文件主干，转换为小写并去除空格
    stem = Path(str(file_name or "")).stem.casefold().strip()
    # 将空格、点、下划线、连字符替换为单个空格
    stem = re.sub(r"[\s._-]+", " ", stem)
    # 删除末尾的括号及其内容，支持中英文括号
    stem = re.sub(r"\s*[\(\[\uFF08\u3010].*?[\)\]\uFF09\u3011]\s*$", "", stem)
    # 去除首尾空格后返回
    return stem.strip()


def _lyrics_file_bracket_count(file_name: str) -> int:
    """统计文件名（去后缀）中括号对的数量。
    
    此函数用于分析给定的文件名，提取其不包含后缀的文件主名部分，
    并利用正则表达式查找其中所有匹配的、由多种括号符号构成的子串。
    
    参数：
        file_name (str): 需要分析的文件名，可以包含后缀。
        
    返回值：
        int: 文件主名中找到的匹配括号对的数量。
    """
    # 获取文件的主干部分（去除文件后缀），若文件名为空或None则视为空字符串
    stem = Path(str(file_name or "")).stem
    # 使用正则表达式查找所有被括号对（包含英文()、[]，以及中文（）、【】）包围的子串，并计算其数量
    return len(re.findall(r"[\(\[\uFF08\u3010].*?[\)\]\uFF09\u3011]", stem))


def _lyrics_row_key(row: dict) -> str:
    """根据歌词行数据生成用于分组的唯一键。

    此函数从输入的字典中提取特定字段，并将它们连接成一个以竖线分隔的字符串，
    用于作为后续处理（如去重、分组）的键。

    Args:
        row (dict): 包含歌词信息的字典。如果传入非字典类型，将使用一个空字典作为默认值。

    Returns:
        str: 由 review_id, lyrics_source, lyrics_id 拼接而成的字符串，首尾可能的竖线会被移除。
    """
    # 确保 payload 是字典类型，如果输入 row 不是字典，则默认使用空字典，避免后续操作报错
    payload = row if isinstance(row, dict) else {}
    # 从字典中安全地获取三个字段的值（若字段不存在或值为空则使用空字符串），并用竖线连接
    return "|".join(
        [
            str(payload.get("review_id", "") or ""),
            str(payload.get("lyrics_source", "") or ""),
            str(payload.get("lyrics_id", "") or ""),
        ]
    ).strip("|") # 去除拼接后字符串首尾可能存在的竖线


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
        self._lyrics_row_controls = {}
        self._lyrics_review_order = []
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
                row_key = _lyrics_row_key(row)
                if row_key:
                    self._lyrics_row_controls[row_key] = row_ctrl
                self._lyrics_review_order.append(dict(row))
                host.addWidget(row_ctrl["container"])

            row_ops_host = QWidget()
            row_ops = QHBoxLayout(row_ops_host)
            row_ops.setContentsMargins(0, 2, 0, 0)
            row_ops.setSpacing(8)
            btn_invert = QPushButton("反选")
            btn_same = QPushButton("这是相同歌词")
            btn_diff = QPushButton("这是不同歌词")
            btn_merge = QPushButton("合并展示的歌词")
            btn_save = QPushButton("保存勾选的文件")
            btn_cancel = QPushButton("取消导入")
            row_ops.addWidget(btn_invert)
            row_ops.addWidget(btn_same)
            row_ops.addWidget(btn_diff)
            row_ops.addWidget(btn_merge)
            row_ops.addWidget(btn_save)
            row_ops.addWidget(btn_cancel)
            row_ops.addStretch(1)
            host.addWidget(row_ops_host)

            controls = {"group_key": group_key, "rows": row_controls}
            self._lyrics_group_controls[group_key] = controls
            btn_invert.clicked.connect(lambda _=False, g=controls: self._invert_lyrics_group(g))
            btn_same.clicked.connect(lambda _=False, g=controls: self._apply_lyrics_preset_same_for_group(g))
            btn_diff.clicked.connect(lambda _=False, g=controls: self._apply_lyrics_preset_diff_for_group(g))
            btn_merge.clicked.connect(lambda _=False, g=controls: self._merge_preview_lyrics_for_group(g))
            btn_save.clicked.connect(lambda _=False, g=controls: self._save_lyrics_group(g))
            btn_cancel.clicked.connect(lambda _=False, g=controls: self._cancel_lyrics_group(g))
            self._register_dynamic_button(btn_invert)
            self._register_dynamic_button(btn_same)
            self._register_dynamic_button(btn_diff)
            self._register_dynamic_button(btn_merge)
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
        is_readonly_reference = bool(payload.get("readonly_reference", False))
        if is_readonly_reference:
            checkbox.setChecked(True)
            checkbox.setEnabled(False)
        btn_reveal = QPushButton("📁")
        btn_reveal.setFixedWidth(34)
        lbl_file = _ClickableLabel(str(payload.get("lyrics_file", "") or ""))
        lbl_file.setMinimumWidth(180)
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
        row_layout.addWidget(btn_reveal)
        row_layout.addWidget(lbl_file, 3)
        row_layout.addWidget(lbl_score)
        outer.addWidget(top)

        reason_row = QWidget()
        reason_layout = QHBoxLayout(reason_row)
        reason_layout.setContentsMargins(34, 0, 0, 0)
        reason_layout.setSpacing(6)
        reason_layout.addWidget(lbl_reason, 1)
        outer.addWidget(reason_row)

        link_bind_row = _ClickableFrame()
        link_bind_row.setFrameShape(QFrame.Shape.NoFrame)
        link_bind_row.setStyleSheet("QFrame{background:transparent;border:none;}")
        bind_layout = QHBoxLayout(link_bind_row)
        bind_layout.setContentsMargins(34, 0, 0, 0)
        bind_layout.setSpacing(6)
        bind_icon = QLabel("🔗")
        suggest_text = str(payload.get("suggest_track", "") or "").strip()
        if is_readonly_reference:
            bind_default = "库内歌词参考（只读）"
        elif suggest_text:
            bind_default = f"当前已绑：{suggest_text}"
        else:
            bind_default = "点击绑定数据库歌曲"
        bind_text = QLabel(bind_default)
        bind_text.setStyleSheet("color:#5d6f86;")
        bind_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        bind_text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        bind_layout.addWidget(bind_icon)
        bind_layout.addWidget(bind_text, 1)
        bind_layout.addStretch(1)
        outer.addWidget(link_bind_row)

        row_ctrl: dict[str, object] = {
            "row": payload,
            "container": container,
            "checkbox": checkbox,
            "bind_text_label": bind_text,
        }

        def _preview() -> None:
            self._on_lyrics_row_clicked(payload)

        def _edit_mapping() -> None:
            self._map_lyrics_row(payload, chain_next=True)

        checkbox.clicked.connect(lambda _checked=False: _preview())
        top.clicked.connect(_preview)
        btn_reveal.clicked.connect(lambda _=False, p=dict(payload): self._reveal_lyrics_file(p))
        if not is_readonly_reference:
            link_bind_row.clicked.connect(_edit_mapping)
        return row_ctrl

    def _reveal_lyrics_file(self, row: dict) -> None:
        """
        功能：在资源管理器中显示歌词文件的位置。
        参数：
            row: dict，包含存储路径信息的字典，预期键包括"storage_relpath"。
        返回值：无。
        """
        storage_rel = str((row or {}).get("storage_relpath", "") or "").strip()  # 安全获取存储路径字符串，处理None或空值
        if not storage_rel:  # 如果存储路径为空，直接返回，避免后续操作
            return
        target = Path(self.facade.library_root) / storage_rel  # 构建目标文件的完整路径，使用库根目录和相对路径拼接
        try:
            if target.exists():  # 如果目标文件存在，在资源管理器中选择该文件
                subprocess.Popen(["explorer", "/select,", str(target)])
            elif target.parent.exists():  # 否则，如果父目录存在，在资源管理器中打开该目录
                subprocess.Popen(["explorer", str(target.parent)])
        except Exception:  # 捕获所有异常，静默返回，避免程序中断
            return

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
        effective: list[tuple[dict, dict]] = []
        for row, row_ctrl in zip(rows, row_controls):
            checkbox = row_ctrl.get("checkbox")
            if isinstance(checkbox, QCheckBox) and checkbox.isEnabled():
                checkbox.setChecked(False)
                effective.append((row, row_ctrl))
        if not effective:
            return

        pairs = list(effective)
        rows_eff = [r for r, _ctrl in pairs]
        if self._lyrics_group_same_file(rows_eff):
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
    def _lyrics_title_hint(self, row: dict) -> str:
        """从字典行中提取歌词标题提示。
    
        功能：尝试从输入字典中获取歌词标题，若无则从预览内容或源文件名中提取。
        参数：row - 包含歌词信息的字典，可能为None或空字典。
        返回值：提取到的歌词标题字符串。
        """
        # 尝试从字典中获取lyrics_title字段，若row为None则使用空字典，确保不会报错
        title = str((row or {}).get("lyrics_title", "") or "").strip()
        # 如果成功获取到非空标题，直接返回
        if title:
            return title
    
        # 如果没有标题，尝试从preview字段获取内容
        preview = str((row or {}).get("preview", "") or "")
        # 遍历预览内容的前30行，寻找类似[ti:xxx]格式的标题标记
        for line in preview.splitlines()[:30]:
            s = str(line or "").strip()  # 处理可能为空的行
            low = s.casefold()  # 转换为小写便于比较
            # 检查是否符合[ti:xxx]的格式
            if low.startswith("[ti:") and s.endswith("]"):
                # 截取[ti:和]之间的内容并去除两端空格
                return s[4:-1].strip()
    
        # 如果以上方法都未找到标题，则使用lyrics_source字段的文件名部分
        source = str((row or {}).get("lyrics_source", "") or "")
        # Path(source).stem 获取文件名（不含扩展名）
        return Path(source).stem.strip()

    def _update_lyrics_bind_label(self, row: dict, track_id: str | None) -> None:
        """更新歌词绑定标签的显示文本。

        参数：
            row (dict): 歌词行的字典。
            track_id (str | None): 曲目ID，可以是字符串或None。

        返回值：
            None: 无返回值。
        """
        row_key = _lyrics_row_key(row)  # 从行字典生成键
        if not row_key:  # 如果没有键，直接返回
            return
        row_ctrl = self._lyrics_row_controls.get(row_key) if isinstance(self._lyrics_row_controls, dict) else None  # 安全获取行控件，检查_controls是否为字典
        if not isinstance(row_ctrl, dict):  # 如果行控件不是字典，返回
            return
        label = row_ctrl.get("bind_text_label")  # 获取绑定文本标签
        if not isinstance(label, QLabel):  # 如果标签不是QLabel实例，返回
            return
        if track_id:  # 如果有曲目ID
            track = self._track_map.get(str(track_id)) if isinstance(getattr(self, "_track_map", {}), dict) else None  # 安全获取曲目信息，检查_track_map属性
            if isinstance(track, dict):  # 如果曲目是字典
                title = str(track.get("title", "") or "")  # 获取标题，处理None或空字符串
                artist = str(track.get("artist", "") or "")  # 获取艺术家，处理None或空字符串
                label.setText(f"当前已绑：{artist or 'Unknown Artist'} - {title or 'Unknown Title'}")  # 设置标签文本，显示绑定的歌曲信息
                return  # 设置后返回
        label.setText("点击绑定数据库歌曲")  # 如果没有track_id或曲目无效，设置提示文本

    def _next_lyrics_row_for_chain(self, row: dict) -> dict | None:
        key = _lyrics_row_key(row)
        if not key:
            return None
        order = self._lyrics_review_order if isinstance(self._lyrics_review_order, list) else []
        for idx, item in enumerate(order):
            if _lyrics_row_key(item) != key:
                continue
            if idx + 1 < len(order):
                return dict(order[idx + 1])
            return None
        return None

    def _map_lyrics_row(self, row: dict, *, chain_next: bool = True) -> None:
        """打开歌曲选择窗口并修改歌词绑定，支持按当前顺序连续处理。"""
        if self._lyrics_map_dialog_open:
            return
        from musearc.ui.review_page import _TrackPickerDialog

        current = dict(row or {})
        self._lyrics_map_dialog_open = True
        try:
            while current:
                lyrics_id = str(current.get("lyrics_id", "") or "")
                if not lyrics_id:
                    QMessageBox.warning(self, "修改建议歌曲", "当前行没有有效 lyrics_id。")
                    return
                picker = _TrackPickerDialog(
                    self,
                    self.facade,
                    initial_query=self._lyrics_title_hint(current),
                    lyrics_preview_text=self._read_lyrics_text(current),
                    preselected_track_id=str(current.get("suggest_track_id", "") or ""),
                )
                if picker.exec() != QDialog.DialogCode.Accepted:
                    return
                self.facade.set_primary_track_for_lyrics(lyrics_id, picker.selected_track_id)
                selected_tid = str(picker.selected_track_id or "")
                current["suggest_track_id"] = selected_tid
                if selected_tid:
                    track = self._track_map.get(selected_tid) if isinstance(getattr(self, "_track_map", {}), dict) else {}
                    if isinstance(track, dict):
                        title = str(track.get("title", "") or "")
                        artist = str(track.get("artist", "") or "")
                        current["suggest_track"] = f"{artist or 'Unknown Artist'} - {title or 'Unknown Title'}"
                else:
                    current["suggest_track"] = ""
                self._update_lyrics_bind_label(current, picker.selected_track_id)
                if not chain_next:
                    return
                nxt = self._next_lyrics_row_for_chain(current)
                if not nxt:
                    return
                current = nxt
        finally:
            self._lyrics_map_dialog_open = False

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
            if isinstance(checkbox, QCheckBox) and checkbox.isEnabled():
                checkbox.setChecked(True)

    def _invert_lyrics_group(self, group: dict) -> None:
        """\u5bf9\u5f53\u524d\u6b4c\u8bcd\u5ba1\u67e5\u7ec4\u6267\u884c\u53cd\u9009\u3002"""
        entries = group.get("rows") if isinstance(group, dict) else []
        for row_ctrl in entries if isinstance(entries, list) else []:
            checkbox = row_ctrl.get("checkbox") if isinstance(row_ctrl, dict) else None
            if isinstance(checkbox, QCheckBox) and checkbox.isEnabled():
                checkbox.setChecked(not checkbox.isChecked())

    def _merge_preview_lyrics_for_group(self, group: dict) -> None:
        """将当前预览区最近两条歌词按时间轴合并到前者，并移除后者。"""
        rows = list(self._preview_rows) if isinstance(self._preview_rows, deque) else []
        if len(rows) < 2:
            QMessageBox.information(self, "合并歌词", "请先在当前组内点击两条歌词用于预览。")
            return

        first = dict(rows[-2] or {})
        second = dict(rows[-1] or {})
        group_key = str((group or {}).get("group_key", "") or "")
        first_key = str(first.get("group_title", "") or first.get("group_key", "") or "")
        second_key = str(second.get("group_title", "") or second.get("group_key", "") or "")
        if group_key and (first_key != group_key or second_key != group_key):
            QMessageBox.warning(self, "合并歌词", "请在同一组内选择两条歌词后再执行合并。")
            return

        primary_id = str(first.get("lyrics_id", "") or "")
        secondary_id = str(second.get("lyrics_id", "") or "")
        if not primary_id or not secondary_id or primary_id == secondary_id:
            QMessageBox.warning(self, "合并歌词", "当前两条记录无效，无法执行合并。")
            return

        first_name = str(first.get("lyrics_file", "") or first.get("file_name", "") or primary_id)
        second_name = str(second.get("lyrics_file", "") or second.get("file_name", "") or secondary_id)
        answer = QMessageBox.question(
            self,
            "合并歌词",
            f"将【{second_name}】合并到【{first_name}】并移除后者，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        review_ids = []
        second_review_id = str(second.get("review_id", "") or "")
        if second_review_id:
            review_ids.append(second_review_id)
        try:
            self.facade.merge_lyrics_for_review(primary_id, secondary_id, resolve_review_ids=review_ids)
        except Exception as exc:
            QMessageBox.warning(self, "合并歌词", f"合并失败: {exc}")
            return

        self._preview_rows.clear()
        self._preview_rows.append(first)
        self.reload_reviews(force_refresh_refs=True)
        self.review_changed.emit()

    def _save_lyrics_group(self, group: dict) -> None:
        """\u4fdd\u5b58\u5f53\u524d\u6b4c\u8bcd\u5ba1\u67e5\u7ec4\u52fe\u9009\u7ed3\u679c\u3002"""
        status_by_review: dict[str, bool] = {}
        restore_lyrics_ids: set[str] = set()
        bind_actions: list[tuple[str, str | None]] = []
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
                lyrics_id = str(row.get("lyrics_id", "") or "")
                if lyrics_id:
                    bind_track_id = str(row.get("suggest_track_id", "") or "").strip()
                    bind_actions.append((lyrics_id, bind_track_id or None))
        if not status_by_review:
            return
        if restore_lyrics_ids:
            self.facade.restore_lyrics(sorted(restore_lyrics_ids))
        for lyrics_id, track_id in bind_actions:
            self.facade.set_primary_track_for_lyrics(lyrics_id, track_id)
        resolved_ids = [rid for rid, keep in status_by_review.items() if keep]
        ignored_ids = [rid for rid, keep in status_by_review.items() if not keep]
        if resolved_ids:
            self.facade.resolve_reviews(resolved_ids, status="resolved")
        if ignored_ids:
            self.facade.resolve_reviews(ignored_ids, status="ignored")
        self.reload_reviews(force_refresh_refs=True)
        self.review_changed.emit()

    def _cancel_lyrics_group(self, group: dict) -> None:
        """\u53d6\u6d88\u5bfc\u5165\u5f53\u524d\u6b4c\u8bcd\u5ba1\u67e5\u7ec4\u3002"""
        ids = self._review_ids_for_group(group)
        if not ids:
            return
        self.facade.resolve_reviews(ids, status="ignored")
        self.reload_reviews()
        self.review_changed.emit()

