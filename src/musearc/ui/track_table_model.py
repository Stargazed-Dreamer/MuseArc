from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont

from musearc.core.pinyin import first_letter


def _safe_int_value(value, default: int = 0) -> int:
    if isinstance(value, (list, tuple, dict, set)):
        return default
    try:
        return int(value)
    except Exception:
        return default


def _safe_bool(value) -> bool:
    if isinstance(value, (list, tuple, set, dict)):
        return bool(len(value))
    try:
        return bool(int(value))
    except Exception:
        return bool(value)


def format_mmss(value) -> str:
    try:
        sec = int(float(value))
    except Exception:
        sec = 0
    m = sec // 60
    s = sec % 60
    return f"{m:02d}:{s:02d}"


def basename(path_text: str) -> str:
    if not path_text:
        return ""
    text = str(path_text).replace("\\", "/")
    return text.split("/")[-1]


def _path_parent_label(path_text: str) -> str:
    text = str(path_text or "").replace("\\", "/").strip("/")
    if not text:
        return "(空)"
    parts = [p for p in text.split("/") if p and not p.endswith(":")]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else "(空)"


def _marker_for_state(state: str) -> str:
    if state == "asc":
        return "↑"
    if state == "desc":
        return "↓"
    return "·"


class TrackTableModel(QAbstractTableModel):
    track_field_edited = Signal(str, str, object)

    BASE_COLUMNS = [
        ("custom_order", "自定义排序", True),
        ("file_name", "文件名", True),
        ("title", "标题", True),
        ("artist", "艺术家", True),
        ("preference_level", "喜好(1-10)", True),
        ("duration_mmss", "时长", False),
        ("lyrics_file_name", "歌词文件名", False),
        ("language_kind", "语言", True),
        ("album", "专辑", True),
        ("source_fullpath", "Source Path", False),
        ("storage_relpath", "Storage Path", False),
        ("format", "格式", False),
        ("track_id", "数据库ID", False),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.raw_tracks: list[dict] = []
        self.sort_rules: list[dict] = []
        self.sort_state_map: dict[str, str] = {}
        self.custom_order_enabled: bool = False
        self.group_by: str | None = None
        self.collapsed_group_keys: set[str] = set()
        self.display_rows: list[dict] = []
        self.visual_selected_track_ids: set[str] = set()
        self.confirm_empty_edit_callback: Callable[[str, str], bool] | None = None
        self.tag_fields: list[str] = []
        self.columns: list[tuple[str, str, bool]] = list(self.BASE_COLUMNS)

    def set_tag_fields(self, tag_fields: list[str]) -> None:
        unique: list[str] = []
        for name in tag_fields:
            text = str(name).strip()
            if not text or text in unique:
                continue
            unique.append(text)
        self.tag_fields = unique
        tag_cols = [(f"tag:{name}", name, True) for name in self.tag_fields]
        self.columns = list(self.BASE_COLUMNS[:-1]) + tag_cols + [self.BASE_COLUMNS[-1]]
        self._rebuild_display()
        if self.columns:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.columns) - 1)

    def set_custom_order_enabled(self, enabled: bool) -> None:
        self.custom_order_enabled = bool(enabled)
        if self.columns:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.columns) - 1)
        self._rebuild_display()

    def set_header_sort_states(self, state_map: dict[str, str]) -> None:
        self.sort_state_map = dict(state_map)
        if self.columns:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.columns) - 1)

    def column_key(self, index: int) -> str:
        if 0 <= index < len(self.columns):
            return self.columns[index][0]
        return ""

    def column_index(self, key: str) -> int:
        for idx, (col_key, _, _editable) in enumerate(self.columns):
            if col_key == key:
                return idx
        return -1

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.columns)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.display_rows)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            key, label, _editable = self.columns[section]
            if key == "custom_order":
                label = "自定义排序" if self.custom_order_enabled else "收藏"
            state = self.sort_state_map.get(key, "off")
            return f"{label} {_marker_for_state(state)}"
        return str(section + 1)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        row_obj = self.display_rows[index.row()]
        key = self.columns[index.column()][0]

        if role == Qt.ItemDataRole.DisplayRole:
            if row_obj["kind"] == "group":
                if index.column() == 0:
                    collapsed = row_obj["group_key"] in self.collapsed_group_keys
                    symbol = "▸" if collapsed else "▾"
                    return f"{symbol} {row_obj['group_label']} ({row_obj['group_count']}) {'─' * 18}"
                return ""

            track = row_obj["track"]
            if key == "custom_order":
                is_favorite = bool(track.get("is_favorite"))
                heart = "♥" if is_favorite else ""
                if self.custom_order_enabled and bool(track.get("_entry_editable")):
                    order_value = _safe_int_value(track.get("entry", 0), 0)
                    return f"{heart} {order_value}" if heart else f"  {order_value}"
                return heart

            value = str(self._value_for_key(track, key))
            if self.group_by and key == "file_name":
                return f"  {value}"
            return value

        if role == Qt.ItemDataRole.EditRole:
            if row_obj["kind"] == "group":
                return None
            track = row_obj["track"]
            return self._value_for_key(track, key)

        if role == Qt.ItemDataRole.ToolTipRole:
            if row_obj["kind"] == "group":
                return "单击可选中分组，双击可折叠/展开分组。"
            track = row_obj["track"]
            if key == "custom_order":
                return "收藏状态 + 自定义排序" if self.custom_order_enabled else "收藏状态"
            return str(self._value_for_key(track, key))

        if role == Qt.ItemDataRole.FontRole and row_obj["kind"] == "group":
            font = QFont()
            font.setBold(True)
            return font

        if role == Qt.ItemDataRole.ForegroundRole and row_obj["kind"] == "group":
            return QColor(38, 60, 82)

        if role == Qt.ItemDataRole.BackgroundRole:
            if row_obj["kind"] == "group":
                return QColor(236, 241, 246)
            track_id = str(row_obj["track"].get("track_id", ""))
            if track_id and track_id in self.visual_selected_track_ids:
                return QColor(85, 170, 255, 110)

        if role == Qt.ItemDataRole.TextAlignmentRole and key in {"preference_level", "duration_mmss"}:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        row_obj = self.display_rows[index.row()]
        if row_obj["kind"] == "group":
            return base
        key = self.columns[index.column()][0]
        editable = self._is_editable_key(key, row_obj["track"])
        return base | Qt.ItemFlag.ItemIsEditable if editable else base

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        row_obj = self.display_rows[index.row()]
        if row_obj["kind"] != "track":
            return False

        key = self.columns[index.column()][0]
        track = row_obj["track"]
        if not self._is_editable_key(key, track):
            return False

        track_id = str(track.get("track_id", ""))
        old_value = self._value_for_key(track, key)

        if key == "preference_level":
            try:
                parsed = int(value)
            except Exception:
                return False
            parsed = max(1, min(10, parsed))
        elif key == "custom_order":
            try:
                parsed = int(str(value).strip())
            except Exception:
                return False
        else:
            parsed = str(value).strip()
            if str(old_value) == parsed:
                return False
            if parsed == "" and str(old_value).strip() != "" and self.confirm_empty_edit_callback:
                if not self.confirm_empty_edit_callback(track_id, key):
                    return False

        if key == "custom_order":
            if _safe_int_value(track.get("entry", 0), 0) == _safe_int_value(parsed, 0):
                return False
            track["entry"] = _safe_int_value(parsed, 0)
            emit_key = "custom_order"
            emit_value = _safe_int_value(parsed, 0)
        elif key.startswith("tag:"):
            if str(old_value) == str(parsed):
                return False
            tag_name = key.split(":", 1)[1]
            tags = dict(track.get("tags", {}))
            if parsed.strip():
                tags[tag_name] = parsed
            else:
                tags.pop(tag_name, None)
            track["tags"] = tags
            track[key] = parsed
            emit_key = key
            emit_value = parsed
        else:
            if str(old_value) == str(parsed):
                return False
            track[key] = parsed
            emit_key = key
            emit_value = parsed

        self._rebuild_display()
        if track_id:
            self.track_field_edited.emit(track_id, emit_key, emit_value)
        return True

    def set_tracks(self, rows: list[dict]) -> None:
        prepared: list[dict] = []
        for row in rows:
            item = dict(row)
            item["duration_mmss"] = format_mmss(item.get("duration_sec", 0))
            item["lyrics_file_name"] = basename(item.get("lyrics_source", ""))
            if not item.get("file_name"):
                item["file_name"] = basename(item.get("source_relpath", "")) or basename(item.get("source_fullpath", ""))
            item["language_kind"] = str(item.get("language_kind") or "unknown")
            try:
                item["preference_level"] = max(1, min(10, int(item.get("preference_level", 5))))
            except Exception:
                item["preference_level"] = 5
            try:
                item["entry"] = int(item.get("entry", 0) or 0)
            except Exception:
                item["entry"] = 0
            item["_entry_editable"] = bool(item.get("_entry_editable", False))
            item["is_favorite"] = _safe_bool(item.get("is_favorite", 0))
            item["format"] = (
                str(item.get("format") or item.get("storage_format") or item.get("source_ext") or "")
                .replace(".", "")
                .lower()
            )
            tags = item.get("tags", {})
            if not isinstance(tags, dict):
                tags = {}
            item["tags"] = tags
            for name in self.tag_fields:
                item[f"tag:{name}"] = str(tags.get(name, ""))
            prepared.append(item)
        self.raw_tracks = prepared
        self._rebuild_display()

    def set_sort_rules(self, rules: list[dict]) -> None:
        self.sort_rules = [dict(rule) for rule in rules]
        self._rebuild_display()

    def set_group_by(self, key: str | None) -> None:
        self.group_by = key if key and key != "none" else None
        self._rebuild_display()

    def set_confirm_empty_edit_callback(self, callback: Callable[[str, str], bool] | None) -> None:
        self.confirm_empty_edit_callback = callback

    def set_visual_selected_track_ids(self, track_ids: set[str]) -> None:
        self.visual_selected_track_ids = set(track_ids)
        if not self.display_rows:
            return
        top = self.index(0, 0)
        bottom = self.index(len(self.display_rows) - 1, len(self.columns) - 1)
        self.dataChanged.emit(top, bottom)

    def apply_value_to_tracks(self, track_ids: set[str], key: str, value) -> None:
        ids = {str(v) for v in track_ids if str(v)}
        if not ids:
            return
        changed = False
        for track in self.raw_tracks:
            track_id = str(track.get("track_id", ""))
            if track_id not in ids:
                continue
            if key == "custom_order":
                try:
                    parsed = int(value)
                except Exception:
                    continue
                track["entry"] = parsed
                changed = True
                continue
            if key == "preference_level":
                try:
                    parsed = max(1, min(10, int(value)))
                except Exception:
                    continue
                track["preference_level"] = parsed
                changed = True
                continue
            if key.startswith("tag:"):
                text = str(value).strip()
                tag_name = key.split(":", 1)[1]
                tags = dict(track.get("tags", {}))
                if text:
                    tags[tag_name] = text
                else:
                    tags.pop(tag_name, None)
                track["tags"] = tags
                track[key] = text
                changed = True
                continue
            text = str(value).strip()
            track[key] = text
            changed = True
        if changed:
            self._rebuild_display()

    def _sort_tracks(self, rows: list[dict]) -> list[dict]:
        items = list(rows)
        active = [r for r in self.sort_rules if r.get("state") in {"asc", "desc"}]
        if not active:
            default_key = "custom_order" if self.custom_order_enabled and any(bool(r.get("_entry_editable")) for r in rows) else "file_name"
            active = [{"key": default_key, "state": "asc"}]
        for rule in reversed(active):
            key = str(rule.get("key"))
            reverse = rule.get("state") == "desc"
            items.sort(key=lambda x, k=key: self._sort_value(x, k), reverse=reverse)
        return items

    def _sort_value(self, row: dict, key: str):
        mapped = "duration_sec" if key == "duration_mmss" else key
        if mapped == "custom_order":
            mapped = "entry" if self.custom_order_enabled else "is_favorite"
        if mapped in {"duration_sec", "preference_level", "entry"}:
            try:
                return float(row.get(mapped, 0))
            except Exception:
                return 0.0
        if mapped == "is_favorite":
            return 1.0 if bool(row.get("is_favorite")) else 0.0
        if mapped == "lyrics_file_name":
            return str(row.get("lyrics_file_name") or basename(row.get("lyrics_source", ""))).casefold()
        return str(row.get(mapped, "") or "").casefold()

    def _group_rows(self, rows: list[dict]) -> list[dict]:
        if not self.group_by:
            return [{"kind": "track", "track": row} for row in rows]

        groups: dict[str, list[dict]] = defaultdict(list)
        labels: dict[str, str] = {}
        for row in rows:
            gk, label = self._group_key_label(row, self.group_by)
            groups[gk].append(row)
            labels[gk] = label

        keys = sorted(groups.keys(), key=lambda k: labels.get(k, k).casefold())
        display: list[dict] = []
        for gk in keys:
            rows_in_group = groups[gk]
            display.append(
                {
                    "kind": "group",
                    "group_key": gk,
                    "group_label": labels.get(gk, gk),
                    "group_count": len(rows_in_group),
                }
            )
            if gk in self.collapsed_group_keys:
                continue
            for row in rows_in_group:
                display.append({"kind": "track", "group_key": gk, "track": row})
        return display

    def _group_key_label(self, row: dict, key: str) -> tuple[str, str]:
        if key == "custom_order":
            if self.custom_order_enabled:
                value = _safe_int_value(row.get("entry", 0), 0)
                return f"entry:{value}", f"排序 {value}"
            fav = bool(row.get("is_favorite"))
            return ("fav:1", "已收藏") if fav else ("fav:0", "未收藏")

        if key == "duration_sec":
            sec = float(row.get("duration_sec", 0) or 0)
            if sec < 10:
                return "dur:0-10", "<10s"
            if sec < 60:
                return "dur:10-60", "10s~1min"
            if sec < 300:
                return "dur:1-5", "1~5min"
            if sec < 600:
                return "dur:5-10", "5~10min"
            if sec < 1800:
                return "dur:10-30", "10~30min"
            return "dur:30+", "30min+"

        if key in {"file_name", "title"}:
            lang = str(row.get("language_kind") or "unknown")
            initial = first_letter(str(row.get(key) or ""))
            return f"name:{lang}:{initial}", f"{lang}/{initial}"

        if key == "preference_level":
            try:
                level = max(1, min(10, int(row.get("preference_level", 5))))
            except Exception:
                level = 5
            return f"pref:{level}", f"喜好 {level}"

        if key in {"source_fullpath", "storage_relpath", "source_relpath"}:
            parent = _path_parent_label(str(row.get(key, "")))
            return f"dir:{key}:{parent}", f"目录/{parent}"

        if key == "track_id":
            value = str(row.get("track_id", ""))
            prefix = value[:2] if len(value) >= 2 else value or "(空)"
            return f"id:{prefix}", prefix

        if key.startswith("tag:"):
            value = str(row.get(key, "") or "(空)")
            return f"{key}:{value}", value

        value = str(row.get(key, "") or "(空)")
        return f"{key}:{value}", value

    def _rebuild_display(self) -> None:
        sorted_rows = self._sort_tracks(self.raw_tracks)
        built = self._group_rows(sorted_rows)
        self.beginResetModel()
        self.display_rows = built
        self.endResetModel()

    def _is_editable_key(self, key: str, track: dict) -> bool:
        if key == "custom_order":
            return bool(self.custom_order_enabled and track.get("_entry_editable"))
        if key.startswith("tag:"):
            return True
        return key in {"file_name", "title", "artist", "preference_level", "language_kind", "album"}

    def _value_for_key(self, track: dict, key: str):
        if key == "custom_order":
            if self.custom_order_enabled:
                return _safe_int_value(track.get("entry", 0), 0)
            return 1 if bool(track.get("is_favorite")) else 0
        if key == "duration_mmss":
            return track.get("duration_mmss", format_mmss(track.get("duration_sec", 0)))
        if key == "lyrics_file_name":
            return track.get("lyrics_file_name", basename(track.get("lyrics_source", "")))
        if key.startswith("tag:"):
            return str(track.get(key, ""))
        return track.get(key, "")

    def is_group_row(self, row: int) -> bool:
        if row < 0 or row >= len(self.display_rows):
            return False
        return self.display_rows[row].get("kind") == "group"

    def toggle_group_row(self, row: int) -> bool:
        if not self.is_group_row(row):
            return False
        gk = self.display_rows[row]["group_key"]
        if gk in self.collapsed_group_keys:
            self.collapsed_group_keys.remove(gk)
        else:
            self.collapsed_group_keys.add(gk)
        self._rebuild_display()
        return True

    def group_track_ids(self, row: int) -> list[str]:
        if not self.is_group_row(row) or not self.group_by:
            return []
        gk = self.display_rows[row]["group_key"]
        out: list[str] = []
        for item in self._sort_tracks(self.raw_tracks):
            item_gk, _ = self._group_key_label(item, self.group_by)
            if item_gk == gk and item.get("track_id"):
                out.append(str(item["track_id"]))
        return out

    def row_indexes_for_track_ids(self, track_ids: set[str]) -> list[int]:
        out: list[int] = []
        for idx, row_obj in enumerate(self.display_rows):
            if row_obj.get("kind") != "track":
                continue
            track_id = str(row_obj["track"].get("track_id", ""))
            if track_id in track_ids:
                out.append(idx)
        return out

    def track_for_row(self, row: int) -> dict | None:
        if row < 0 or row >= len(self.display_rows):
            return None
        row_obj = self.display_rows[row]
        if row_obj.get("kind") != "track":
            return None
        return row_obj.get("track")

    def selected_track_ids_from_rows(self, rows: list[int]) -> list[str]:
        out: list[str] = []
        for row in rows:
            track = self.track_for_row(row)
            if track and track.get("track_id"):
                out.append(str(track["track_id"]))
        return out
