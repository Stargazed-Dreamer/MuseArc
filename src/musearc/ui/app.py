from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .theme import apply_musearc_theme


def run_ui(library_path: str | None = None) -> int:
    """启动图形用户界面（GUI）应用程序的主函数。

    该函数初始化并运行一个完整的GUI应用，包括应用主题设置、主窗口创建和事件循环管理。

    参数:
        library_path (str | None): 图书馆数据库的文件路径。默认为 None，表示使用程序默认路径或创建新数据库。

    返回:
        int: 应用程序的退出状态码。通常，0 表示正常退出，非零值表示异常退出。
    """
    # 创建一个Qt应用程序实例，sys.argv包含命令行参数，供Qt解析
    app = QApplication(sys.argv)
    # 应用预设的Musearc主题到应用程序中，以统一界面风格
    apply_musearc_theme(app)
    # 使用传入的library_path（如果存在）创建并初始化主窗口
    window = MainWindow(library_path=library_path)
    # 将创建的主窗口显示到屏幕上
    window.show()
    # 启动Qt应用程序的事件循环，并返回其退出状态码。这行代码会阻塞直到窗口关闭。
    return app.exec()
