from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QWidget


def _fmt_ms(ms: int) -> str:
    sec = max(0, int(ms // 1000))
    return f"{sec // 60:02d}:{sec % 60:02d}"


class InlinePlayerBar(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._paths: list[str] = []
        self._labels: list[str] = []
        self._index = -1
        self._duration_ms = 0
        self._dragging = False
        self._pending_seek_ms = 0

        self.audio_output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.7)

        self._media_devices = QMediaDevices(self)
        self._bind_audio_device_signals()
        self._switch_to_default_output_device()

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(8)

        self.btn_prev = QPushButton("上一首")
        self.btn_play = QPushButton("暂停")
        self.btn_next = QPushButton("下一首")
        self.slider_progress = QSlider(Qt.Orientation.Horizontal)
        self.slider_progress.setRange(0, 0)
        self.label_time = QLabel("00:00 / 00:00")
        self.slider_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(70)
        self.btn_close = QPushButton("关闭")

        root.addWidget(self.btn_prev)
        root.addWidget(self.btn_play)
        root.addWidget(self.btn_next)
        root.addWidget(self.slider_progress, 1)
        root.addWidget(self.label_time)
        root.addWidget(QLabel("音量"))
        root.addWidget(self.slider_volume)
        root.addWidget(self.btn_close)

        self.btn_prev.clicked.connect(self.play_prev)
        self.btn_play.clicked.connect(self.toggle_play_pause)
        self.btn_next.clicked.connect(self.play_next)
        self.btn_close.clicked.connect(self.stop_and_hide)
        self.slider_volume.valueChanged.connect(self._on_volume_changed)
        self.slider_progress.sliderPressed.connect(self._on_seek_pressed)
        self.slider_progress.sliderReleased.connect(self._on_seek_released)

        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)

        self.hide()

    def _bind_audio_device_signals(self) -> None:
        default_changed = getattr(self._media_devices, "defaultAudioOutputChanged", None)
        if default_changed is not None:
            try:
                default_changed.connect(self._switch_to_default_output_device)
            except Exception:
                pass
        outputs_changed = getattr(self._media_devices, "audioOutputsChanged", None)
        if outputs_changed is not None:
            try:
                outputs_changed.connect(self._switch_to_default_output_device)
            except Exception:
                pass

    def _switch_to_default_output_device(self, *_args) -> None:
        try:
            device = QMediaDevices.defaultAudioOutput()
            self.audio_output.setDevice(device)
        except Exception:
            return

    def play_queue(
        self,
        paths: list[str],
        *,
        start_index: int = 0,
        start_sec: int = 0,
        labels: list[str] | None = None,
    ) -> bool:
        cleaned: list[str] = []
        cleaned_labels: list[str] = []
        for idx, raw in enumerate(paths):
            text = str(raw or "").strip()
            if not text:
                continue
            p = Path(text)
            if not p.exists():
                continue
            cleaned.append(str(p.resolve()))
            if labels and idx < len(labels):
                cleaned_labels.append(str(labels[idx] or p.name))
            else:
                cleaned_labels.append(p.name)
        if not cleaned:
            return False
        self._paths = cleaned
        self._labels = cleaned_labels
        self._index = max(0, min(int(start_index), len(self._paths) - 1))
        self._pending_seek_ms = max(0, int(start_sec) * 1000)
        self.show()
        self.raise_()
        self._load_and_play_current()
        return True

    def clear_queue(self) -> None:
        self._paths = []
        self._labels = []
        self._index = -1
        self._pending_seek_ms = 0

    def release_for_file_ops(self) -> None:
        self.stop_and_hide()

    def stop_and_hide(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.clear_queue()
        self.hide()

    def play_next(self) -> None:
        if not self._paths:
            return
        if self._index + 1 >= len(self._paths):
            self.player.stop()
            return
        self._index += 1
        self._pending_seek_ms = 0
        self._load_and_play_current()

    def play_prev(self) -> None:
        if not self._paths:
            return
        if self._index - 1 < 0:
            self._index = 0
        else:
            self._index -= 1
        self._pending_seek_ms = 0
        self._load_and_play_current()

    def toggle_play_pause(self) -> None:
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if not self._paths:
                return
            self.player.play()

    def _load_and_play_current(self) -> None:
        if not self._paths or self._index < 0 or self._index >= len(self._paths):
            return
        target = Path(self._paths[self._index])
        if not target.exists():
            self.play_next()
            return
        self._switch_to_default_output_device()
        self.player.setSource(QUrl.fromLocalFile(str(target)))
        self.player.play()
        if self._pending_seek_ms > 0:
            QTimer.singleShot(120, self._apply_pending_seek_if_needed)

    def _apply_pending_seek_if_needed(self) -> None:
        if self._pending_seek_ms <= 0:
            return
        duration = int(self.player.duration() or 0)
        if duration <= 0:
            QTimer.singleShot(120, self._apply_pending_seek_if_needed)
            return
        self.player.setPosition(min(self._pending_seek_ms, duration))
        self._pending_seek_ms = 0

    def _on_volume_changed(self, value: int) -> None:
        self.audio_output.setVolume(max(0.0, min(1.0, float(value) / 100.0)))

    def _on_seek_pressed(self) -> None:
        self._dragging = True

    def _on_seek_released(self) -> None:
        self._dragging = False
        self.player.setPosition(int(self.slider_progress.value()))

    def _on_position_changed(self, value: int) -> None:
        if not self._dragging:
            with QSignalBlocker(self.slider_progress):
                self.slider_progress.setValue(int(value))
        self.label_time.setText(f"{_fmt_ms(value)} / {_fmt_ms(self._duration_ms)}")

    def _on_duration_changed(self, value: int) -> None:
        self._duration_ms = max(0, int(value))
        with QSignalBlocker(self.slider_progress):
            self.slider_progress.setRange(0, self._duration_ms)
        self.label_time.setText(f"{_fmt_ms(self.player.position())} / {_fmt_ms(self._duration_ms)}")

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play.setText("暂停")
        else:
            self.btn_play.setText("播放")

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self._pending_seek_ms > 0:
            self.player.setPosition(self._pending_seek_ms)
            self._pending_seek_ms = 0
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_next()
