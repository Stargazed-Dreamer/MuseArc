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
    candidates: list[Path] = []
    env_bin = str(os.environ.get("MUSEARC_CHROMAPRINT_BIN", "")).strip()
    if env_bin:
        candidates.append(Path(env_bin))
    here = Path(__file__).resolve()
    # repo root: <root>/src/musearc/infra/media/fingerprint.py
    candidates.append(here.parents[4] / "tools" / "chromaprint" / "bin")

    for cand in candidates:
        try:
            folder = cand.expanduser().resolve()
        except Exception:
            continue
        if not folder.exists():
            continue
        lib_a = folder / "chromaprint.dll"
        lib_b = folder / "libchromaprint.dll"
        if not lib_a.exists() and not lib_b.exists():
            continue

        # pychromaprint probes both names through find_library on Windows.
        # Keep both aliases to maximize discoverability.
        if lib_b.exists() and not lib_a.exists():
            try:
                shutil.copyfile(lib_b, lib_a)
            except Exception:
                pass

        path_value = os.environ.get("PATH", "")
        if str(folder) not in path_value.split(os.pathsep):
            os.environ["PATH"] = f"{folder}{os.pathsep}{path_value}" if path_value else str(folder)

        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(folder))
            except Exception:
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

    def _can_use_chromaprint(self) -> bool:
        if _acoustid is None:
            return False
        have = bool(getattr(_acoustid, "have_chromaprint", False))
        if have:
            return True
        # If acoustid was imported before DLL path was prepared, retry once by reloading.
        try:
            reloaded = importlib.reload(_acoustid)
        except Exception:
            return False
        return bool(getattr(reloaded, "have_chromaprint", False))

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
        if _acoustid is None or not self._can_use_chromaprint():
            return 0.0
        a_bytes = bytes(self.decode_vector(payload_a))
        b_bytes = bytes(self.decode_vector(payload_b))
        if not a_bytes or not b_bytes:
            return 0.0
        try:
            score = float(_acoustid.compare_fingerprints((0, a_bytes), (0, b_bytes)))
            return max(0.0, min(1.0, score))
        except Exception:
            return 0.0
