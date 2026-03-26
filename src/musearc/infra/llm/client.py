from __future__ import annotations

import json
from dataclasses import dataclass

import requests

from musearc.config.models import LmStudioConfig


@dataclass(slots=True)
class LlmMatchResult:
    score: float
    reason: str


class LmStudioMatcher:
    def __init__(self, cfg: LmStudioConfig):
        self.cfg = cfg

    def score_audio_lyrics(self, audio_payload: dict, lyrics_payload: dict) -> LlmMatchResult | None:
        if not self.cfg.enabled:
            return None

        prompt = {
            "task": "score_audio_lyrics_match",
            "constraints": [
                "return strict json only",
                "score in [0,1]",
                "reason should be short and factual",
            ],
            "audio": audio_payload,
            "lyrics": lyrics_payload,
        }

        try:
            response = requests.post(
                self.cfg.endpoint,
                timeout=self.cfg.timeout_sec,
                headers={"Content-Type": "application/json"},
                json={
                    "model": self.cfg.model,
                    "temperature": 0.0,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a strict scoring engine for music metadata and lyrics matching. "
                                "Output json only: {\"score\":number,\"reason\":string}."
                            ),
                        },
                        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            score = float(parsed.get("score", 0.0))
            score = max(0.0, min(1.0, score))
            reason = str(parsed.get("reason", "llm_scored"))
            return LlmMatchResult(score=score, reason=reason)
        except Exception:
            return None
