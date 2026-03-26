from __future__ import annotations

import base64
import hashlib
import math
from typing import Iterable

import numpy as np

from musearc.core.models import Fingerprint

from .audio_io import decode_audio


class AcousticFingerprintEngine:
    """
    Stronger tonal-transition fingerprint:
    - decode through PyAV backend (DLL)
    - compute chroma sequence
    - compare with shift-aware sequence matching + histogram cosine
    """

    version: int = 2

    def fingerprint_file(self, audio_path) -> Fingerprint:
        decoded = decode_audio(audio_path, target_rate=22050, target_layout="mono")
        vector = self._fingerprint_vector(decoded.samples, decoded.sample_rate)
        payload = self.encode_vector(vector)
        digest = hashlib.sha1(payload.encode("ascii")).hexdigest()
        return Fingerprint(version=self.version, vector=vector, digest=digest)

    def _fingerprint_vector(self, samples: np.ndarray, sample_rate: int) -> list[int]:
        if samples.size < sample_rate * 5:
            return []

        frame_size = 4096
        hop = 1024
        if samples.size < frame_size:
            return []

        samples = samples.astype(np.float32, copy=False)
        window = np.hanning(frame_size).astype(np.float32)

        freqs = np.fft.rfftfreq(frame_size, d=1.0 / sample_rate)
        bin_to_class = np.full(freqs.shape[0], -1, dtype=np.int16)
        valid = (freqs >= 40.0) & (freqs <= 5000.0)
        midi = np.rint(69.0 + 12.0 * np.log2(freqs[valid] / 440.0)).astype(np.int16)
        bin_to_class[valid] = midi % 12

        seq: list[int] = []
        for start in range(0, samples.size - frame_size + 1, hop):
            frame = samples[start : start + frame_size]
            spectrum = np.abs(np.fft.rfft(frame * window))
            chroma = np.zeros(12, dtype=np.float32)

            for idx, cls in enumerate(bin_to_class):
                if cls >= 0:
                    chroma[cls] += spectrum[idx]

            norm = float(np.linalg.norm(chroma))
            if norm < 1e-8:
                continue

            chroma /= norm
            top2 = np.argsort(chroma)[-2:]
            dominant = int(top2[-1])
            secondary = int(top2[-2])
            code = dominant * 12 + secondary
            seq.append(code)

        return seq[:8192]

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
        a = self.decode_vector(payload_a)
        b = self.decode_vector(payload_b)
        return self.vector_similarity(a, b)

    @staticmethod
    def vector_similarity(a: list[int], b: list[int], max_shift: int = 72) -> float:
        if len(a) < 32 or len(b) < 32:
            return 0.0

        a_arr = np.asarray(a, dtype=np.int16)
        b_arr = np.asarray(b, dtype=np.int16)

        align_best = 0.0
        for shift in range(-max_shift, max_shift + 1):
            if shift >= 0:
                xa = a_arr[shift:]
                yb = b_arr[: xa.size]
            else:
                yb = b_arr[-shift:]
                xa = a_arr[: yb.size]

            n = min(xa.size, yb.size)
            if n < 32:
                continue
            xa = xa[:n]
            yb = yb[:n]

            exact = xa == yb
            same_dom = (xa // 12) == (yb // 12)
            score = (float(exact.sum()) + 0.6 * float((same_dom & ~exact).sum())) / n
            if score > align_best:
                align_best = score

        hist_a = np.bincount(a_arr, minlength=144).astype(np.float32)
        hist_b = np.bincount(b_arr, minlength=144).astype(np.float32)
        na = float(np.linalg.norm(hist_a))
        nb = float(np.linalg.norm(hist_b))
        hist_score = 0.0 if na == 0.0 or nb == 0.0 else float(hist_a.dot(hist_b) / (na * nb))

        len_ratio = min(len(a), len(b)) / max(len(a), len(b))
        return max(0.0, min(1.0, 0.55 * align_best + 0.35 * hist_score + 0.10 * len_ratio))
