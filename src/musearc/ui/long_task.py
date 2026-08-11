from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event

from PySide6.QtCore import QEventLoop, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]
TaskCallable = Callable[[ProgressCallback, CancelCheck], object]


@dataclass(slots=True)
class TaskRunResult:
    cancelled: bool
    result: object | None = None
    error: Exception | None = None


def _format_eta(seconds: float | None) -> str:
    """将秒数格式化为时间字符串。

    参数：
        seconds (float | None): 要格式化的秒数。可以是浮点数或None。

    返回值：
        str: 格式化后的时间字符串，如 "HH:MM:SS" 或 "MM:SS"。如果seconds为None或负数，返回"--:--"。
    """
    if seconds is None or seconds < 0:  # 处理无效输入，返回默认时间
        return "--:--"
    sec = int(round(seconds))  # 将秒数四舍五入并转为整数
    mm, ss = divmod(max(0, sec), 60)  # 使用divmod计算分钟和秒，max(0, sec)确保非负
    hh, mm = divmod(mm, 60)  # 再次使用divmod计算小时和剩余分钟
    if hh > 0:  # 如果有小时部分，返回 HH:MM:SS 格式
        return f"{hh:02d}:{mm:02d}:{ss:02d}"
    return f"{mm:02d}:{ss:02d}"  # 否则返回 MM:SS 格式


class _TaskWorker(QObject):
    progress = Signal(int, int, str, float)
    finished = Signal(object, object, bool)

    def __init__(self, task: TaskCallable):
        """
        初始化 Task 对象。

        参数:
            task (TaskCallable): 需要执行的任务函数或可调用对象。

        返回值:
            无
        """
        super().__init__()  # 调用父类的初始化方法
        self._task = task  # 存储需要执行的任务
        self._cancel = Event()  # 创建一个事件对象，用于控制任务的取消
        self._start = 0.0  # 初始化任务开始时间

    def cancel(self) -> None:
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def _emit_progress(self, current: int, total: int, message: str = "") -> None:
        elapsed = max(0.0, time.monotonic() - self._start)
        total_safe = max(1, int(total or 1))
        curr_safe = max(0, min(total_safe, int(current or 0)))
        self.progress.emit(curr_safe, total_safe, str(message or ""), elapsed)

    def run(self) -> None:
        """执行一个后台任务，并在完成后发出信号。

        该方法记录开始时间，然后运行`_task`方法。任务完成后（无论是成功还是失败），
        都会通过`finished`信号发出结果。如果任务在运行期间被请求取消，则信号会传递相应的标志。

        参数：
            self (Worker): Worker类实例，包含任务和相关的状态。

        返回值：
            None: 该方法不直接返回结果，结果通过`self.finished`信号传递。
        """
        # 记录任务开始时的单调时钟时间（不受系统时间调整影响）
        self._start = time.monotonic()
        try:
            # 调用任务方法，并传入进度发射函数和取消状态检查函数
            result = self._task(self._emit_progress, self.is_cancelled)
            # 任务成功完成，通过信号发送结果（result），无异常（None），以及最终的取消状态
            self.finished.emit(result, None, self._cancel.is_set())
        except Exception as exc:  # pragma: no cover
            # pragma: no cover 表示该分支在正常单元测试中难以触发，例如任务内部抛出的未知异常
            # 任务执行出错，通过信号发送结果（None），异常信息（exc），以及最终的取消状态
            self.finished.emit(None, exc, self._cancel.is_set())


class _TaskProgressDialog(QDialog):
    cancel_clicked = Signal()

    def __init__(self, parent: QWidget, title: str):
        super().__init__(parent)
        self.setWindowTitle(str(title or "正在处理"))
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        self.label = QLabel("准备中...")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.btn_cancel = QPushButton("取消")

        root.addWidget(self.label)
        root.addWidget(self.bar)
        root.addWidget(self.btn_cancel)

        self.btn_cancel.clicked.connect(self.cancel_clicked.emit)

    def mark_cancelling(self) -> None:
        """
        标记取消操作，禁用取消按钮并显示加载状态。
        参数：
            self: 实例自身，表示当前对象实例。
        返回值：
            无。
        """
        self.btn_cancel.setEnabled(False)  # 禁用取消按钮，防止重复操作
        self.btn_cancel.setText("正在取消...")  # 更新按钮文本，提示用户操作正在进行

    def update_state(self, current: int, total: int, message: str, elapsed: float) -> None:
        """更新进度状态并刷新界面显示。

        根据当前进度、总数、消息和已用时间，计算百分比和预计剩余时间，
        并更新进度条与状态标签。

        参数:
            current (int): 当前已完成的项目数。
            total (int): 项目总数。
            message (str): 要显示的状态消息。
            elapsed (float): 已经过去的时间（秒）。

        返回:
            None: 此方法不返回任何值。
        """
        total_safe = max(1, int(total or 1))  # 确保总数为正整数，避免除零错误
        curr_safe = max(0, min(total_safe, int(current or 0)))  # 将当前值限制在[0, total_safe]区间内
        pct = int(round(curr_safe * 100.0 / total_safe))  # 计算完成百分比并四舍五入取整
        self.bar.setValue(max(0, min(100, pct)))  # 设置进度条值，限制在0-100之间

        eta = None
        if curr_safe > 0:  # 只有在已完成进度大于0时才计算预计剩余时间
            eta = max(0.0, elapsed * (total_safe - curr_safe) / curr_safe)  # 基于已完成部分推算剩余时间
        text = f"{message or '正在处理'} | {curr_safe}/{total_safe} ({pct}%) | ETA {_format_eta(eta)}"  # 格式化状态文本
        self.label.setText(text)  # 更新标签显示


def run_modal_task(parent: QWidget, title: str, task: TaskCallable) -> TaskRunResult:
    """Run task in worker thread with delayed modal progress and cancellation.

    - Delay modal for 3 seconds.
    - Show integer progress + ETA when task is still running.
    - Refresh UI every 250ms to keep update overhead low.
    """
    thread = QThread(parent)
    worker = _TaskWorker(task)
    worker.moveToThread(thread)

    latest = {"current": 0, "total": 100, "message": "准备中...", "elapsed": 0.0}
    done = {"value": False}
    output = TaskRunResult(cancelled=False, result=None, error=None)

    dialog = _TaskProgressDialog(parent, title)
    dialog.hide()

    show_timer = QTimer(parent)
    show_timer.setSingleShot(True)
    show_timer.setInterval(3000)

    refresh_timer = QTimer(parent)
    refresh_timer.setInterval(250)

    def _on_progress(current: int, total: int, message: str, elapsed: float) -> None:
        latest["current"] = int(current)
        latest["total"] = int(total)
        latest["message"] = str(message or "")
        latest["elapsed"] = float(elapsed or 0.0)

    def _refresh() -> None:
        if done["value"] or not dialog.isVisible():
            return
        dialog.update_state(
            int(latest["current"]),
            int(latest["total"]),
            str(latest["message"]),
            float(latest["elapsed"]),
        )

    def _show_if_needed() -> None:
        if done["value"]:
            return
        dialog.update_state(
            int(latest["current"]),
            int(latest["total"]),
            str(latest["message"]),
            float(latest["elapsed"]),
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_done(result: object, error: object, cancelled: bool) -> None:
        done["value"] = True
        output.cancelled = bool(cancelled)
        output.result = result
        output.error = error if isinstance(error, Exception) else None

    def _request_cancel() -> None:
        # IMPORTANT:
        # worker.cancel must run immediately; queued cross-thread delivery may never run
        # while worker thread is busy in long CPU loop.
        worker.cancel()
        latest["message"] = "正在取消..."
        output.cancelled = True
        if dialog.isVisible():
            dialog.mark_cancelling()

    worker.progress.connect(_on_progress)
    worker.finished.connect(_on_done)
    worker.finished.connect(thread.quit)
    thread.started.connect(worker.run)

    dialog.cancel_clicked.connect(_request_cancel)
    show_timer.timeout.connect(_show_if_needed)
    refresh_timer.timeout.connect(_refresh)

    loop = QEventLoop(parent)
    worker.finished.connect(loop.quit)

    thread.start()
    show_timer.start()
    refresh_timer.start()
    loop.exec()

    refresh_timer.stop()
    show_timer.stop()
    dialog.close()
    thread.wait(2000)
    worker.deleteLater()
    thread.deleteLater()
    return output


def make_chunked_task(
    ids: list[str],
    *,
    chunk_size: int,
    message: str,
    step: Callable[[list[str]], int | None],
) -> TaskCallable:
    """创建一个分块执行任务的函数。

    该函数接收一个ID列表，将其分割成指定大小的块，并逐步处理这些块，
    同时报告进度和处理结果。

    Args:
        ids: 要处理的ID列表，将被转换为字符串并过滤空值。
        chunk_size: 每个块的大小，至少为1。
        message: 在进度报告中显示的进度消息。
        step: 处理每个块的回调函数，接收一个字符串列表，返回影响的记录数或None。

    Returns:
        一个可调用的任务函数，该函数接收进度回调和取消检查，执行分块处理并返回结果字典。
    """
    # 将ids列表中的每个元素转换为字符串，过滤掉空值
    seq = [str(v) for v in ids if str(v)]
    # 确保chunk_size至少为1，处理可能为None或0的情况
    size = max(1, int(chunk_size or 1))

    def _task(progress: ProgressCallback, is_cancelled: CancelCheck) -> dict:
        """实际执行任务的内部函数。

        Args:
            progress: 进度回调函数，用于报告当前进度。
            is_cancelled: 检查是否取消的回调函数。

        Returns:
            包含处理结果的字典，包括processed（已处理数）、affected（影响数）、cancelled（是否取消）。
        """
        total = len(seq)
        processed = 0
        affected = 0
        # 如果没有需要处理的数据，直接报告完成并返回空结果
        if total <= 0:
            progress(1, 1, message)
            return {"processed": 0, "affected": 0, "cancelled": False}
        # 按照size大小分块遍历序列
        for start in range(0, total, size):
            # 检查是否已取消任务，如果已取消则提前结束循环
            if is_cancelled():
                break
            # 获取当前块的数据
            chunk = seq[start : start + size]
            # 调用step函数处理当前块，获取影响的记录数（可能为None，需要转换为整数）
            result = step(chunk)
            # 累加影响的记录数，如果result为None则视为0
            affected += int(result or 0)
            # 累加已处理的记录数
            processed += len(chunk)
            # 报告当前进度
            progress(processed, total, message)
        # 判断任务是否被取消：检查取消条件且还有未处理的数据
        cancelled = bool(is_cancelled() and processed < total)
        return {"processed": processed, "affected": affected, "cancelled": cancelled}

    # 返回任务函数
    return _task
