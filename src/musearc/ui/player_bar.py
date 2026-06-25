from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QWidget


def _fmt_ms(ms: int) -> str:
    """
    将毫秒数格式化为分钟和秒的字符串。

    参数：
        ms (int): 毫秒数，表示时间间隔。

    返回值：
        str: 格式化为“MM:SS”的字符串，其中MM是分钟，SS是秒。
    """
    sec = max(0, int(ms // 1000))  # 将毫秒转换为秒，并确保非负
    return f"{sec // 60:02d}:{sec % 60:02d}"  # 格式化为“MM:SS”字符串


class InlinePlayerBar(QWidget):
    def __init__(self, parent: QWidget | None = None):
        """音频播放器控件初始化。

        该方法初始化播放器的所有内部状态、音频组件、用户界面（UI）控件，
        并将信号与槽函数进行连接。
        """
        super().__init__(parent)
        # 初始化播放列表、播放状态和拖拽相关变量
        self._paths: list[str] = []  # 存储音频文件路径的列表
        self._labels: list[str] = []  # 存储对应曲目标签的列表
        self._index = -1  # 当前播放的曲目索引，-1 表示无曲目
        self._duration_ms = 0  # 当前媒体的总时长（毫秒）
        self._dragging = False  # 标记进度条是否正在被拖拽
        self._pending_seek_ms = 0  # 存储拖拽时暂存的跳转位置（毫秒）

        # 创建音频输出设备和媒体播放器核心对象，并进行关联
        self.audio_output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        # 设置默认音量（0.7 即 70%）
        self.audio_output.setVolume(0.7)

        # 初始化媒体设备监控，并绑定设备变化信号，然后切换到默认音频输出设备
        self._media_devices = QMediaDevices(self)
        self._bind_audio_device_signals()
        self._switch_to_default_output_device()

        # 设置主水平布局
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)  # 设置布局的外边距
        root.setSpacing(8)  # 设置控件之间的间距

        # 创建并配置用户界面控件
        self.btn_prev = QPushButton("上一首")
        self.btn_play = QPushButton("暂停")
        self.btn_next = QPushButton("下一首")
        self.slider_progress = QSlider(Qt.Orientation.Horizontal)  # 播放进度滑块
        self.slider_progress.setRange(0, 0)  # 初始范围设为0
        self.label_time = QLabel("00:00 / 00:00")  # 显示当前时间/总时间的标签
        self.slider_volume = QSlider(Qt.Orientation.Horizontal)  # 音量滑块
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(70)  # 默认音量与音频输出设备同步
        self.btn_close = QPushButton("关闭")

        # 将控件添加到布局中
        root.addWidget(self.btn_prev)
        root.addWidget(self.btn_play)
        root.addWidget(self.btn_next)
        # 设置进度滑块的拉伸因子为1，使其占据更多空间
        root.addWidget(self.slider_progress, 1)
        root.addWidget(self.label_time)
        root.addWidget(QLabel("音量"))
        root.addWidget(self.slider_volume)
        root.addWidget(self.btn_close)

        # 连接用户界面控件的信号到对应的槽函数
        self.btn_prev.clicked.connect(self.play_prev)
        self.btn_play.clicked.connect(self.toggle_play_pause)
        self.btn_next.clicked.connect(self.play_next)
        self.btn_close.clicked.connect(self.stop_and_hide)
        self.slider_volume.valueChanged.connect(self._on_volume_changed)
        self.slider_progress.sliderPressed.connect(self._on_seek_pressed)
        self.slider_progress.sliderReleased.connect(self._on_seek_released)

        # 连接媒体播放器的信号到状态更新槽函数
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)

        # 初始状态下隐藏整个播放器控件
        self.hide()

    def _bind_audio_device_signals(self) -> None:
        """绑定音频设备相关信号，当设备变化时自动切换到默认设备。

        该方法连接两个信号：默认音频输出设备改变信号和音频输出设备列表改变信号，
        当这些信号触发时，调用切换默认输出设备的方法。

        Args:
            self: 实例自身。

        Returns:
            None
        """
        # 获取默认音频输出设备改变信号（可能不存在）
        default_changed = getattr(self._media_devices, "defaultAudioOutputChanged", None)
        if default_changed is not None:
            try:
                # 将信号连接到切换默认输出设备的方法
                default_changed.connect(self._switch_to_default_output_device)
            except Exception:
                pass
        # 获取音频输出设备列表改变信号（可能不存在）
        outputs_changed = getattr(self._media_devices, "audioOutputsChanged", None)
        if outputs_changed is not None:
            try:
                # 将信号连接到切换默认输出设备的方法
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
        """播放列表中的下一个项目。

        此方法根据内部索引 (`_index`) 推进播放列表。如果列表为空或已是最后一个项目，
        则会提前返回或停止播放。

        参数:
            self: 类实例。

        返回:
            None: 该方法不返回任何值。
        """
        # 如果播放列表为空，则直接返回，不做任何操作。
        if not self._paths:
            return

        # 检查当前索引是否已到达或超过列表末尾。
        if self._index + 1 >= len(self._paths):
            # 已经是最后一个项目，停止播放器。
            self.player.stop()
            return

        # 将当前播放索引指向下一个项目。
        self._index += 1
        # 重置待处理的定位时间（单位：毫秒），确保从新项目的开头播放。
        self._pending_seek_ms = 0
        # 加载并播放索引 `_index` 所指向的新项目。
        self._load_and_play_current()

    def play_prev(self) -> None:
        """播放列表中的上一个文件。

        此方法将当前索引移动到列表中的上一个文件并加载播放。
        如果路径列表为空或已是第一个文件，则进行相应处理。

        参数：无（通过self访问实例状态）。
        返回值：无。
        """
        # 如果路径列表为空，则直接返回，因为没有文件可播放
        if not self._paths:
            return
        # 检查索引减1后是否小于0，以确定是否可移动到上一个文件
        if self._index - 1 < 0:
            # 如果已是第一个文件，将索引设为0，保持当前状态
            self._index = 0
        else:
            # 否则，将索引减1以指向列表中的上一个文件
            self._index -= 1
        # 重置待处理的寻道时间为0，清除之前的寻道状态
        self._pending_seek_ms = 0
        # 加载并播放当前索引对应的文件
        self._load_and_play_current()

    def toggle_play_pause(self) -> None:
        """切换媒体播放与暂停状态。
        检查当前播放状态，若正在播放则暂停；若未播放，则检查是否有可用媒体路径，有则开始播放。
        Args:
            self: 类实例自身。
        Returns:
            None: 此方法不返回任何值。
        """
        # 获取当前播放器的状态
        state = self.player.playbackState()
        # 如果当前状态是正在播放，则执行暂停操作
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        # 如果当前状态不是正在播放（可能是暂停、停止等）
        else:
            # 检查存储媒体路径的列表是否为空
            if not self._paths:
                # 如果没有可用的媒体路径，则直接返回，不执行播放操作
                return
            # 有可用路径，开始播放媒体
            self.player.play()

    def _load_and_play_current(self) -> None:
        """
        加载并播放当前索引（self._index）对应的媒体文件。
        这是一个内部方法，负责实际播放逻辑。
        参数：无。
        返回：无（None）。
        """
        # 检查路径列表是否为空，或索引是否有效（不在[0, len-1]范围内）
        if not self._paths or self._index < 0 or self._index >= len(self._paths):
            return  # 条件不满足，直接返回，不做任何操作
        # 获取当前索引对应的文件路径，并转换为Path对象
        target = Path(self._paths[self._index])
        # 检查目标文件是否存在
        if not target.exists():
            self.play_next()  # 文件不存在，尝试播放列表中的下一个文件
            return
        # 切换到系统默认的音频输出设备
        self._switch_to_default_output_device()
        # 为播放器设置媒体源，将文件路径转换为QUrl格式
        self.player.setSource(QUrl.fromLocalFile(str(target)))
        # 开始播放
        self.player.play()
        # 如果有待处理的播放位置跳转请求（单位：毫秒）
        if self._pending_seek_ms > 0:
            # 延迟120毫秒后执行跳转，等待播放器初始化完成
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
        """位置改变时的处理函数。
    
        当播放位置发生变化时自动调用，用于更新界面控件状态。
    
        Args:
            value: 当前播放位置，单位为毫秒
        
        Returns:
            None
        """
        # 检查是否正在拖动进度条，避免拖动时被其他更新干扰
        if not self._dragging:
            # 阻止进度条控件发出信号，防止无限循环更新
            with QSignalBlocker(self.slider_progress):
                # 更新进度条的值，转为整数类型
                self.slider_progress.setValue(int(value))
        # 更新时间显示标签，格式化为可读的时间字符串
        self.label_time.setText(f"{_fmt_ms(value)} / {_fmt_ms(self._duration_ms)}")

    def _on_duration_changed(self, value: int) -> None:
        """当播放时长发生变化时，更新界面显示和进度条范围。

        Args:
            value (int): 新的播放时长，单位为毫秒。
    
        Returns:
            None: 此方法不返回任何值。
        """
        # 将传入的时长值转换为整数，并确保非负，然后存储为实例属性
        self._duration_ms = max(0, int(value))
        # 使用信号阻塞器，防止在设置进度条范围时触发不必要的信号
        with QSignalBlocker(self.slider_progress):
            # 设置进度条的可选范围从0到总时长
            self.slider_progress.setRange(0, self._duration_ms)
        # 更新时间标签，显示当前播放时间和总时长（均格式化为毫秒）
        self.label_time.setText(f"{_fmt_ms(self.player.position())} / {_fmt_ms(self._duration_ms)}")

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        """响应媒体播放器状态变化，更新播放/暂停按钮的显示文本。

        当媒体播放器的状态（如正在播放、暂停、停止）发生改变时，此槽函数会被调用。
        它会根据新的播放状态，动态地切换界面上播放/暂停按钮的文字，为用户提供直观的反馈。

        Args:
            state (QMediaPlayer.PlaybackState): 一个枚举值，表示媒体播放器的当前状态。

        Returns:
            None: 此方法不返回任何值。
        """
        # 检查当前播放状态是否为“播放中”
        if state == QMediaPlayer.PlaybackState.PlayingState:
            # 状态为播放中时，将按钮文本设为“暂停”，表示点击后将执行暂停操作
            self.btn_play.setText("暂停")
        else:
            # 状态非播放中（如暂停、停止等）时，将按钮文本设为“播放”，表示点击后将开始播放
            self.btn_play.setText("播放")

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        """处理媒体状态变化的事件。

        当媒体状态改变时，根据状态执行相应操作，例如处理媒体加载完成后的跳转或媒体播放结束后的自动播放。

        参数:
            status (QMediaPlayer.MediaStatus): 当前的媒体状态，表示媒体播放过程中的不同阶段。

        返回值:
            None
        """
        # 如果媒体已加载且有待执行的跳转时间，则设置播放器到指定位置，并重置跳转时间
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self._pending_seek_ms > 0:
            self.player.setPosition(self._pending_seek_ms)
            self._pending_seek_ms = 0
        # 如果媒体播放结束，则自动播放下一个媒体
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_next()
