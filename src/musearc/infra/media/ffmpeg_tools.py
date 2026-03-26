from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
import shutil

from .commands import MediaCommandError


def _is_executable(path_text: str | None) -> bool:
    text = str(path_text or "").strip()
    if not text:
        return False
    path = Path(text).expanduser()
    return path.exists() and path.is_file()


def _common_windows_candidates(binary_name: str) -> list[Path]:
    exe = f"{binary_name}.exe"
    out: list[Path] = []
    for root in [
        Path("C:/ffmpeg/bin"),
        Path("C:/Program Files/ffmpeg/bin"),
        Path("C:/Program Files (x86)/ffmpeg/bin"),
    ]:
        out.append(root / exe)
    return out


def _repo_local_candidates(binary_name: str) -> list[Path]:
    exe = f"{binary_name}.exe"
    here = Path(__file__).resolve()
    out: list[Path] = []
    for parent in [here.parent, *here.parents]:
        out.append(parent / "tools" / "ffmpeg" / "bin" / exe)
        out.append(parent / "third_party" / "ffmpeg" / "bin" / exe)
        out.append(parent / "bin" / exe)
    return out


def _from_env(keys: list[str]) -> Path | None:
    for key in keys:
        value = os.environ.get(key)
        if _is_executable(value):
            return Path(str(value)).expanduser().resolve()
    return None


def _resolve_binary(binary_name: str, *, peer_hint: Path | None = None) -> Path | None:
    env_keys = (
        ["FFMPEG_BIN", "MUSEARC_FFMPEG"] if binary_name == "ffmpeg" else ["FFPROBE_BIN", "MUSEARC_FFPROBE"]
    )
    env_path = _from_env(env_keys)
    if env_path is not None:
        return env_path

    if peer_hint is not None and peer_hint.exists():
        sibling = peer_hint.with_name(f"{binary_name}.exe")
        if sibling.exists():
            return sibling.resolve()

    found = shutil.which(binary_name)
    if _is_executable(found):
        return Path(str(found)).resolve()

    for candidate in [*_common_windows_candidates(binary_name), *_repo_local_candidates(binary_name)]:
        if candidate.exists():
            return candidate.resolve()
    return None


@lru_cache(maxsize=1)
def ffmpeg_path() -> Path:
    path = _resolve_binary("ffmpeg")
    if path is None:
        raise MediaCommandError(
            "ffmpeg_not_found:请安装 ffmpeg 并加入 PATH，或设置环境变量 FFMPEG_BIN / MUSEARC_FFMPEG。"
        )
    return path


@lru_cache(maxsize=1)
def ffprobe_path() -> Path:
    peer = None
    try:
        peer = ffmpeg_path()
    except Exception:
        peer = None
    path = _resolve_binary("ffprobe", peer_hint=peer)
    if path is None:
        raise MediaCommandError(
            "ffprobe_not_found:请安装 ffprobe 并加入 PATH，或设置环境变量 FFPROBE_BIN / MUSEARC_FFPROBE。"
        )
    return path
