from __future__ import annotations

import base64
import hashlib
import importlib
import os
from pathlib import Path
import shutil
from typing import Iterable

import numpy as np

from musearc.core.models import Fingerprint

from .audio_io import decode_audio


def _prepare_chromaprint_runtime() -> None:
    """准备Chromaprint运行环境，配置必要的动态链接库路径。
    
    该函数会尝试查找并配置Chromaprint库的运行环境，包括从环境变量指定的路径、
    项目特定工具目录中查找动态链接库，并将其添加到系统PATH环境变量中，
    同时处理Windows系统下的DLL目录注册。
    
    参数:
        无参数
        
    返回值:
        None: 该函数没有返回值，通过修改环境变量来准备运行环境
    """
    candidates: list[Path] = []  # 存储所有可能的Chromaprint二进制文件路径候选
    env_bin = str(os.environ.get("MUSEARC_CHROMAPRINT_BIN", "")).strip()  # 从环境变量获取自定义路径
    if env_bin:  # 如果环境变量有值
        candidates.append(Path(env_bin))  # 添加到候选列表
    here = Path(__file__).resolve()  # 获取当前文件的绝对路径
    # repo root: <root>/src/musearc/infra/media/fingerprint.py
    candidates.append(here.parents[4] / "tools" / "chromaprint" / "bin")  # 添加项目工具目录到候选列表

    for cand in candidates:  # 遍历所有候选路径
        try:
            folder = cand.expanduser().resolve()  # 展开用户目录(~)并解析路径
        except Exception:  # 如果路径解析出错
            continue  # 跳过此候选路径
        if not folder.exists():  # 检查文件夹是否存在
            continue  # 不存在则跳过
        lib_a = folder / "chromaprint.dll"  # Windows下可能的动态链接库名称
        lib_b = folder / "libchromaprint.dll"  # 另一种可能的动态链接库名称
        if not lib_a.exists() and not lib_b.exists():  # 如果两个库文件都不存在
            continue  # 跳过此候选路径

        # pychromaprint probes both names through find_library on Windows.
        # Keep both aliases to maximize discoverability.
        if lib_b.exists() and not lib_a.exists():  # 如果只有libchromaprint.dll存在
            try:
                shutil.copyfile(lib_b, lib_a)  # 复制为chromaprint.dll以保持兼容性
            except Exception:  # 复制失败时静默处理
                pass

        path_value = os.environ.get("PATH", "")  # 获取当前PATH环境变量值
        if str(folder) not in path_value.split(os.pathsep):  # 如果路径不在PATH中
            # 将文件夹路径添加到PATH环境变量的开头（使用系统路径分隔符）
            os.environ["PATH"] = f"{folder}{os.pathsep}{path_value}" if path_value else str(folder)

        if hasattr(os, "add_dll_directory"):  # 检查是否支持add_dll_directory（Windows特有）
            try:
                os.add_dll_directory(str(folder))  # 注册DLL目录到系统
            except Exception:  # 注册失败时静默处理
                pass


_prepare_chromaprint_runtime()

try:
    import acoustid as _acoustid
except Exception:
    _acoustid = None


class AcousticFingerprintEngine:
    """
    Fingerprint backend policy:
    - Use Chromaprint only (through pyacoustid + libchromaprint DLL).
    - If unavailable, fingerprint generation fails and caller should fallback to name-based review.
    """

    def __init__(self) -> None:
        self._backend = "chromaprint"
        self.version = 3
        self._chromaprint_checked = False
        self._chromaprint_available = False

    def _can_use_chromaprint(self) -> bool:
        # Never perform expensive reload checks for every pairwise similarity call.
        # Probe once and cache the result for this engine instance.
        if self._chromaprint_checked:
            return bool(self._chromaprint_available)
        self._chromaprint_checked = True
        if _acoustid is None:
            self._chromaprint_available = False
            return False
        have = bool(getattr(_acoustid, "have_chromaprint", False))
        if have:
            self._chromaprint_available = True
            return True
        # If acoustid was imported before DLL path was prepared, retry once.
        try:
            reloaded = importlib.reload(_acoustid)
            self._chromaprint_available = bool(getattr(reloaded, "have_chromaprint", False))
        except Exception:
            self._chromaprint_available = False
        return bool(self._chromaprint_available)

    @property
    def chromaprint_available(self) -> bool:
        return self._can_use_chromaprint()

    def fingerprint_hash32(self, payload: str) -> int | None:
        """Return Chromaprint-provided 32-bit hash for a stored payload."""
        if not self._can_use_chromaprint():
            return None
        try:
            import chromaprint
        except Exception:
            return None
        try:
            raw = bytes(self.decode_vector(payload))
            if not raw:
                return None
            decoded, _algo = chromaprint.decode_fingerprint(raw)
            return int(chromaprint.hash_fingerprint(decoded))
        except Exception:
            return None

    def fingerprint_file(self, audio_path) -> Fingerprint:
        decoded = decode_audio(audio_path, target_rate=22050, target_layout="mono")
        vector = self._fingerprint_vector(decoded.samples, decoded.sample_rate)
        payload = self.encode_vector(vector)
        digest = hashlib.sha1(payload.encode("ascii")).hexdigest()
        return Fingerprint(version=self.version, vector=vector, digest=digest)

    def _fingerprint_vector(self, samples: np.ndarray, sample_rate: int) -> list[int]:
        return self._fingerprint_vector_chromaprint(samples, sample_rate)

    def _fingerprint_vector_chromaprint(self, samples: np.ndarray, sample_rate: int) -> list[int]:
        if _acoustid is None or not self._can_use_chromaprint():
            return []
        if samples.size < sample_rate * 3:
            return []
        mono = samples.astype(np.float32, copy=False)
        mono = np.clip(mono, -1.0, 1.0)
        pcm16 = (mono * 32767.0).astype(np.int16)
        if pcm16.size <= 0:
            return []
        pcm_bytes = pcm16.tobytes()
        try:
            fp_raw = _acoustid.fingerprint(int(sample_rate), 1, [pcm_bytes], maxlength=180)
        except Exception:
            return []
        if isinstance(fp_raw, (bytes, bytearray)):
            text = bytes(fp_raw).decode("ascii", errors="ignore").strip()
        else:
            text = str(fp_raw or "").strip()
        if not text:
            return []
        return list(text.encode("ascii", errors="ignore"))

    @staticmethod
    def encode_vector(vector: Iterable[int]) -> str:
        bounded = [max(0, min(255, int(v))) for v in vector]
        packed = bytes(bounded)
        return base64.b64encode(packed).decode("ascii")

    @staticmethod
    def decode_vector(payload: str) -> list[int]:
        if not payload:
            return []
        raw = base64.b64decode(payload.encode("ascii"))
        return [int(b) for b in raw]

    def similarity(self, payload_a: str, payload_b: str) -> float:
        """通过Chromaprint音频指纹的比对，计算两个音频之间的相似度。

        Args:
            payload_a (str): 第一个音频的指纹编码字符串。
            payload_b (str): 第二个音频的指纹编码字符串。

        Returns:
            float: 一个介于 0.0 到 1.0 之间的相似度分数。
                   0.0 表示完全不同或无法计算，1.0 表示完全相同。
        """
        # 检查必需的 acoustid 库是否存在，以及当前实例是否支持 chromaprint。
        # 如果条件不满足，无法进行指纹比对，直接返回 0.0。
        if _acoustid is None or not self._can_use_chromaprint():
            return 0.0
        # 将两个字符串格式的指纹 payload 解码并转换为字节序列，以供比对算法使用。
        a_bytes = bytes(self.decode_vector(payload_a))
        b_bytes = bytes(self.decode_vector(payload_b))
        # 如果解码后的字节序列为空，说明输入无效，无法进行比对。
        if not a_bytes or not b_bytes:
            return 0.0
        try:
            # 调用 acoustid 库的 compare_fingerprints 函数进行指纹比对。
            # 函数期望接收元组 (fingerprint_version, fingerprint_bytes)，此处版本号统一用 0。
            score = float(_acoustid.compare_fingerprints((0, a_bytes), (0, b_bytes)))
            # 将比对分数限制在 0.0 到 1.0 的有效范围内。
            return max(0.0, min(1.0, score))
        except Exception:
            # 捕获比对过程中可能发生的任何异常，并返回默认值 0.0。
            return 0.0
