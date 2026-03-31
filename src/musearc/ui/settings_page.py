from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from musearc.app.facade import MuseArcFacade
from musearc.config.store import save_runtime_config


def _apply_button_scale(button: QPushButton, scale: float) -> None:
    button.setMinimumHeight(max(30, int(28 * scale)))


class SettingsPage(QWidget):
    settings_saved = Signal()

    def __init__(self, facade: MuseArcFacade):
        super().__init__()
        self.facade = facade

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.spin_threshold = QSpinBox()
        self.spin_threshold.setRange(1, 1000)

        self.spin_undo = QSpinBox()
        self.spin_undo.setRange(1, 10000)

        self.spin_button_scale = QDoubleSpinBox()
        self.spin_button_scale.setRange(1.0, 2.5)
        self.spin_button_scale.setSingleStep(0.05)

        self.spin_autosave = QSpinBox()
        self.spin_autosave.setRange(1, 120)

        self.combo_delete_mode = QComboBox()
        self.combo_delete_mode.addItem("绑定歌词一起移动到回收站", "move_linked_lyrics")
        self.combo_delete_mode.addItem("仅删除歌曲并解开映射关系", "unlink_only")

        self.combo_player_mode = QComboBox()
        self.combo_player_mode.addItem("内置播放器", "builtin")
        self.combo_player_mode.addItem("外部播放器", "external")

        self.input_player_path = QLineEdit()
        self.btn_pick_player = QPushButton("浏览...")
        player_row = QWidget()
        player_layout = QHBoxLayout(player_row)
        player_layout.setContentsMargins(0, 0, 0, 0)
        player_layout.addWidget(self.input_player_path, 1)
        player_layout.addWidget(self.btn_pick_player)

        self.chk_enable_logs = QCheckBox("启用日志记录（仅保留最近10条）")
        self.chk_empty_confirm = QCheckBox("双击编辑为空时弹窗确认")

        form.addRow("退出多选强制保存阈值", self.spin_threshold)
        form.addRow("撤回最大保留条数", self.spin_undo)
        form.addRow("按钮高度倍率", self.spin_button_scale)
        form.addRow("自动保存间隔(分钟)", self.spin_autosave)
        form.addRow("删除歌曲默认行为", self.combo_delete_mode)
        form.addRow("默认播放方式", self.combo_player_mode)
        form.addRow("外部播放器路径", player_row)
        form.addRow("", self.chk_enable_logs)
        form.addRow("", self.chk_empty_confirm)

        self.btn_save = QPushButton("保存")
        self.btn_save.clicked.connect(self.save_settings)

        root.addLayout(form)
        root.addWidget(self.btn_save)
        root.addStretch(1)

        self.combo_player_mode.currentIndexChanged.connect(self._on_player_mode_changed)
        self.btn_pick_player.clicked.connect(self._pick_external_player)

        self.set_facade(facade)

    def apply_button_scale(self, scale: float) -> None:
        _apply_button_scale(self.btn_save, scale)
        _apply_button_scale(self.btn_pick_player, scale)

    def set_facade(self, facade: MuseArcFacade) -> None:
        self.facade = facade
        cfg = self.facade.get_runtime_config()
        self.spin_threshold.setValue(int(cfg.ui.force_save_threshold))
        self.spin_undo.setValue(int(cfg.ui.undo_max_actions))
        self.spin_button_scale.setValue(float(cfg.ui.button_scale))
        self.spin_autosave.setValue(int(cfg.ui.db_autosave_minutes))

        idx = self.combo_delete_mode.findData(str(cfg.ui.delete_tracks_mode_default or "move_linked_lyrics"))
        self.combo_delete_mode.setCurrentIndex(max(0, idx))

        pidx = self.combo_player_mode.findData(str(cfg.ui.player_mode or "external"))
        self.combo_player_mode.setCurrentIndex(max(0, pidx))

        self.input_player_path.setText(str(cfg.ui.external_player_path or ""))
        self.chk_enable_logs.setChecked(bool(cfg.ui.enable_logs))
        self.chk_empty_confirm.setChecked(bool(cfg.ui.prompt_empty_edit_confirm))
        self._on_player_mode_changed()

    def refresh_page(self) -> None:
        self.set_facade(self.facade)

    def save_settings(self) -> None:
        cfg = self.facade.get_runtime_config()
        cfg.ui.force_save_threshold = int(self.spin_threshold.value())
        cfg.ui.undo_max_actions = int(self.spin_undo.value())
        cfg.ui.button_scale = float(self.spin_button_scale.value())
        cfg.ui.db_autosave_minutes = int(self.spin_autosave.value())
        cfg.ui.delete_tracks_mode_default = str(self.combo_delete_mode.currentData())
        cfg.ui.player_mode = str(self.combo_player_mode.currentData())
        cfg.ui.external_player_path = str(self.input_player_path.text().strip())
        cfg.ui.enable_logs = bool(self.chk_enable_logs.isChecked())
        cfg.ui.prompt_empty_edit_confirm = bool(self.chk_empty_confirm.isChecked())
        save_runtime_config(cfg)
        QMessageBox.information(self, "设置", "设置已保存")
        self.settings_saved.emit()

    def _on_player_mode_changed(self) -> None:
        is_external = str(self.combo_player_mode.currentData()) == "external"
        self.input_player_path.setEnabled(is_external)
        self.btn_pick_player.setEnabled(is_external)

    def _pick_external_player(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择外部播放器可执行文件",
            self.input_player_path.text().strip(),
            "可执行文件 (*.exe);;所有文件 (*.*)",
        )
        if path:
            self.input_player_path.setText(path)
