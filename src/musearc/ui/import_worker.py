from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from musearc.app.facade import MuseArcFacade
from musearc.services.import_runtime import ImportControl


class ImportWorker(QObject):
    progress = Signal(dict)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, library_path: str, source_path: str):
        """
        初始化函数，用于设置库路径、源路径并创建导入控制对象。

        参数:
            library_path (str): 库文件的路径。
            source_path (str): 源文件的路径。

        返回:
            None
        """
        super().__init__()  # 调用父类的初始化方法
        self.library_path = library_path  # 将库路径存储为实例属性
        self.source_path = source_path  # 将源路径存储为实例属性
        self.control = ImportControl()  # 创建导入控制对象

    def request_cancel(self, mode: str) -> None:
        self.control.request_cancel(mode)

    def request_pause(self) -> None:
        self.control.request_pause()

    def request_resume(self) -> None:
        self.control.request_resume()

    def run(self) -> None:
        """
        运行音乐库导入任务。

        该方法初始化MuseArcFacade，并从指定源路径导入音乐文件。
        导入过程中通过信号报告进度、完成和失败状态。

        参数:
            无（除self外，它是实例方法的一部分）

        返回值:
            None（无直接返回值，但通过以下信号通知调用方：
            - progress.emit: 导入进度更新
            - finished.emit: 导入成功完成，附带报告
            - failed.emit: 导入失败，附带错误信息）
        """
        try:
            # 初始化MuseArcFacade门面实例，传入库路径
            facade = MuseArcFacade(self.library_path)

            # 定义内部进度回调函数，用于将导入进度转换为信号发射
            def _on_progress(p):
                # 构建进度信息字典，包含导入批次ID、源路径、阶段等关键状态
                # 注意：file_states字段如果为None则转换为空列表，避免信号传递时出错
                self.progress.emit(
                    {
                        "import_batch_id": p.import_batch_id,
                        "source_path": p.source_path,
                        "stage": p.stage,
                        "current_file": p.current_file,
                        "scanned_files": p.scanned_files,
                        "processed_files": p.processed_files,
                        "imported_tracks": p.imported_tracks,
                        "duplicate_tracks": p.duplicate_tracks,
                        "imported_lyrics": p.imported_lyrics,
                        "matched_lyrics": p.matched_lyrics,
                        "review_items": p.review_items,
                        "errors": p.errors,
                        "resumed": p.resumed,
                        "paused": p.paused,
                        "file_states": p.file_states if p.file_states else [],
                    }
                )

            # 调用门面实例的导入方法，传入源路径、控制参数和进度回调函数
            report = facade.import_from(self.source_path, control=self.control, progress_callback=_on_progress)
            # 导入成功，发射完成信号并附带报告
            self.finished.emit(report)
        except Exception as exc:
            # 捕获所有异常，发射失败信号并附带错误信息字符串
            self.failed.emit(str(exc))
