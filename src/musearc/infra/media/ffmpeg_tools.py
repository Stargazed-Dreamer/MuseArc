from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path

from .commands import MediaCommandError


def _is_executable(path_text: str | None) -> bool:
    """
    检查给定的路径文本是否指向一个可执行文件。

    参数:
        path_text (str | None): 路径文本，可能为字符串或None。

    返回:
        bool: 如果路径存在且是文件则返回True，否则返回False。
    """
    # 将输入转换为字符串，处理None情况，并去除首尾空格
    text = str(path_text or "").strip()
    # 如果处理后的文本为空，返回False
    if not text:
        return False
    # 创建Path对象并展开用户主目录（如~）
    path = Path(text).expanduser()
    # 检查路径是否存在且是文件，返回结果
    return path.exists() and path.is_file()


def _common_windows_candidates(binary_name: str) -> list[Path]:
    """
    功能：生成Windows系统中ffmpeg可执行文件的候选路径列表。
    参数：
        binary_name (str): 二进制文件的名称，例如 "ffmpeg"。
    返回：
        list[Path]: 包含可能的ffmpeg可执行文件路径的列表。
    """
    exe = f"{binary_name}.exe"  # 根据二进制名称构建完整的exe文件名
    out: list[Path] = []  # 初始化一个空列表，用于存储候选路径
    for root in [  # 遍历常见的ffmpeg安装目录
        Path("C:/ffmpeg/bin"),
        Path("C:/Program Files/ffmpeg/bin"),
        Path("C:/Program Files (x86)/ffmpeg/bin"),
    ]:
        out.append(root / exe)  # 将根目录与exe文件名组合成完整路径并添加到列表
    return out  # 返回所有候选路径


def _repo_local_candidates(binary_name: str) -> list[Path]:
    """
    查找当前文件所在目录及其所有父目录下，包含指定可执行文件的候选路径。

    功能说明：
        在当前Python脚本文件所在的目录层级中，搜索可能存在指定可执行文件（如ffmpeg）的路径。
        搜索范围包括从当前目录到最顶层目录的每一级目录，并在每级目录下的三个固定子目录中查找。

    参数：
        binary_name (str): 要查找的可执行文件名（不含扩展名），例如 'ffmpeg'。

    返回值：
        list[Path]: 包含所有可能的可执行文件完整路径的列表。路径为Path对象，包括当前目录及其所有父目录下
                   "tools/ffmpeg/bin"、"third_party/ffmpeg/bin"和"bin"三个子目录中的目标文件。
    """
    # 根据输入的可执行文件名构建完整的文件名（添加.exe扩展名）
    exe = f"{binary_name}.exe"
    # 获取当前Python脚本文件的绝对路径
    here = Path(__file__).resolve()
    # 初始化一个空列表，用于存储所有找到的候选路径
    out: list[Path] = []
    # 遍历当前目录及其所有父目录（从当前目录到根目录）
    for parent in [here.parent, *here.parents]:
        # 在当前父目录下的 tools/ffmpeg/bin 子目录中查找可执行文件
        out.append(parent / "tools" / "ffmpeg" / "bin" / exe)
        # 在当前父目录下的 third_party/ffmpeg/bin 子目录中查找可执行文件
        out.append(parent / "third_party" / "ffmpeg" / "bin" / exe)
        # 在当前父目录下的 bin 子目录中查找可执行文件
        out.append(parent / "bin" / exe)
    # 返回所有候选路径的列表
    return out


def _from_env(keys: list[str]) -> Path | None:
    """
    从环境变量中查找可执行文件的路径。

    参数:
        keys (list[str]): 要检查的环境变量键名列表。

    返回:
        Path | None: 如果找到可执行文件，返回其路径；否则返回 None。
    """
    for key in keys:  # 遍历所有可能的环境变量键
        value = os.environ.get(key)  # 从环境变量中获取对应的值
        if _is_executable(value):  # 检查该值是否代表一个可执行文件
            return Path(str(value)).expanduser().resolve()  # 将值转换为Path对象，扩展用户主目录并解析为绝对路径
    return None  # 如果没有找到可执行文件，返回None


def _resolve_binary(binary_name: str, *, peer_hint: Path | None = None) -> Path | None:
    """解析指定二进制文件的路径。

    功能：通过检查环境变量、对等提示路径、系统PATH以及常见候选路径，来定位二进制文件（如ffmpeg或ffprobe）的完整路径。

    参数：
        binary_name (str): 二进制文件的名称，例如 "ffmpeg" 或 "ffprobe"。
        peer_hint (Path | None): 可选参数，表示对等二进制文件的路径提示。如果提供且存在，将尝试查找相邻的可执行文件。

    返回值：
        Path | None: 如果找到二进制文件，返回其绝对路径（Path对象）；否则返回None。
    """
    # 根据二进制文件名称选择环境变量键，ffmpeg使用FFMPEG_BIN和MUSEARC_FFMPEG，ffprobe使用FFPROBE_BIN和MUSEARC_FFPROBE
    env_keys = (
        ["FFMPEG_BIN", "MUSEARC_FFMPEG"] if binary_name == "ffmpeg" else ["FFPROBE_BIN", "MUSEARC_FFPROBE"]
    )
    # 从环境变量中查找路径
    env_path = _from_env(env_keys)
    # 如果环境变量中有路径，直接返回
    if env_path is not None:
        return env_path

    # 如果提供了对等提示路径且该路径存在，尝试查找相邻的.exe文件
    if peer_hint is not None and peer_hint.exists():
        # 构造相邻的可执行文件名，例如binary_name为"ffmpeg"时，生成"ffmpeg.exe"
        sibling = peer_hint.with_name(f"{binary_name}.exe")
        # 如果相邻的可执行文件存在，返回其绝对路径
        if sibling.exists():
            return sibling.resolve()

    # 使用shutil.which在系统PATH中查找二进制文件
    found = shutil.which(binary_name)
    # 如果找到的文件是可执行的，返回其绝对路径
    if _is_executable(found):
        return Path(str(found)).resolve()

    # 遍历常见候选路径，包括Windows常见路径和仓库本地路径
    for candidate in [*_common_windows_candidates(binary_name), *_repo_local_candidates(binary_name)]:
        # 如果候选路径存在，返回其绝对路径
        if candidate.exists():
            return candidate.resolve()
    # 如果所有方法都未找到，返回None
    return None


@lru_cache(maxsize=1)
def ffmpeg_path() -> Path:
    """
    获取ffmpeg可执行文件的路径。

    功能：
        通过解析二进制名称获取ffmpeg的路径。如果找不到ffmpeg，则抛出异常。
    参数：
        无
    返回值：
        Path: ffmpeg可执行文件的路径。
    """
    # 尝试解析并获取ffmpeg的路径
    path = _resolve_binary("ffmpeg")
    # 如果没有找到ffmpeg，则抛出异常
    if path is None:
        raise MediaCommandError(
            "ffmpeg_not_found:请安装 ffmpeg 并加入 PATH，或设置环境变量 FFMPEG_BIN / MUSEARC_FFMPEG。"
        )
    # 返回找到的ffmpeg路径
    return path


@lru_cache(maxsize=1)
def ffprobe_path() -> Path:
    """获取ffprobe可执行文件的路径。如果没有找到，则抛出MediaCommandError异常。

    参数：无
    返回值：Path对象，表示ffprobe的路径。
    """
    peer = None  # 初始化peer变量为None，用于存储ffmpeg路径的提示
    try:
        peer = ffmpeg_path()  # 尝试调用ffmpeg_path函数获取ffmpeg的路径
    except Exception:
        peer = None  # 如果发生异常，则将peer重置为None，确保后续处理
    path = _resolve_binary("ffprobe", peer_hint=peer)  # 使用_resolve_binary函数解析ffprobe的二进制路径，peer_hint参数提供ffmpeg路径作为提示
    if path is None:  # 检查解析后的路径是否为None，表示未找到ffprobe
        raise MediaCommandError(
            "ffprobe_not_found:请安装 ffprobe 并加入 PATH，或设置环境变量 FFPROBE_BIN / MUSEARC_FFPROBE。"
        )  # 抛出异常，提示用户安装或配置ffprobe
    return path  # 返回找到的ffprobe路径作为Path对象
