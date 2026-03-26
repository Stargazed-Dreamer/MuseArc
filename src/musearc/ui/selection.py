from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SelectionMode(StrEnum):
    NORMAL = "normal"
    MULTI = "multi"


@dataclass(slots=True)
class SelectionController:
    mode: SelectionMode = SelectionMode.NORMAL
    selected_rows: set[int] = field(default_factory=set)
    anchor_row: int | None = None
    focus_row: int | None = None
    saved_snapshots: list[list[int]] = field(default_factory=list)
    saved_this_session: bool = False

    def set_mode(self, mode: SelectionMode, total_rows: int, force_save_threshold: int) -> None:
        if self.mode == SelectionMode.MULTI and mode == SelectionMode.NORMAL:
            if len(self.selected_rows) > force_save_threshold and not self.saved_this_session:
                self.save_snapshot()
        self.mode = mode
        if self.mode == SelectionMode.NORMAL:
            self.selected_rows = {r for r in self.selected_rows if 0 <= r < total_rows}
            if not self.selected_rows and total_rows > 0:
                row = self.focus_row if self.focus_row is not None else 0
                row = max(0, min(total_rows - 1, row))
                self.selected_rows = {row}
                self.anchor_row = row
                self.focus_row = row
        else:
            self.saved_this_session = False

    def normal_click(self, row: int) -> None:
        self.selected_rows = {row}
        self.anchor_row = row
        self.focus_row = row

    def multi_click_toggle(self, row: int) -> None:
        if row in self.selected_rows:
            self.selected_rows.remove(row)
        else:
            self.selected_rows.add(row)
        self.anchor_row = row
        self.focus_row = row

    def multi_toggle_range(self, row_a: int, row_b: int) -> None:
        start = min(row_a, row_b)
        end = max(row_a, row_b)
        for row in range(start, end + 1):
            if row in self.selected_rows:
                self.selected_rows.remove(row)
            else:
                self.selected_rows.add(row)
        self.anchor_row = row_b
        self.focus_row = row_b

    def move_focus(self, total_rows: int, delta: int) -> int:
        if total_rows <= 0:
            self.focus_row = None
            return -1
        base = self.focus_row if self.focus_row is not None else 0
        row = max(0, min(total_rows - 1, base + delta))
        self.focus_row = row
        if self.mode == SelectionMode.NORMAL:
            self.selected_rows = {row}
            self.anchor_row = row
        return row

    def page_focus(self, total_rows: int, visible_rows: int, direction: int) -> int:
        step = max(1, int(visible_rows * 0.7))
        return self.move_focus(total_rows, step * direction)

    def keyboard_activate(self, shift: bool = False) -> None:
        if self.focus_row is None:
            return
        if self.mode == SelectionMode.NORMAL:
            self.normal_click(self.focus_row)
            return
        if shift and self.anchor_row is not None:
            self.multi_toggle_range(self.anchor_row, self.focus_row)
        else:
            self.multi_click_toggle(self.focus_row)

    def save_snapshot(self) -> None:
        snapshot = sorted(self.selected_rows)
        if not snapshot:
            return
        self.saved_snapshots.append(snapshot)
        if len(self.saved_snapshots) > 5:
            self.saved_snapshots = self.saved_snapshots[-5:]
        self.saved_this_session = True

    def load_snapshot(self, index: int) -> None:
        if index < 0 or index >= len(self.saved_snapshots):
            return
        self.selected_rows = set(self.saved_snapshots[index])
        if self.selected_rows:
            self.anchor_row = min(self.selected_rows)
            self.focus_row = self.anchor_row

    def _normalize_for_normal(self, total_rows: int) -> None:
        if total_rows <= 0:
            self.selected_rows.clear()
            self.anchor_row = None
            self.focus_row = None
            return
        if not self.selected_rows:
            row = self.focus_row if self.focus_row is not None else 0
            row = max(0, min(total_rows - 1, row))
            self.selected_rows = {row}
            self.anchor_row = row
            self.focus_row = row
            return
        row = min(self.selected_rows)
        row = max(0, min(total_rows - 1, row))
        self.selected_rows = {row}
        self.anchor_row = row
        self.focus_row = row
