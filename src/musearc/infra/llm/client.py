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
        """
        使用LLM评估音频与歌词的匹配程度并返回评分结果。
    
        参数:
            self: 类实例
            audio_payload (dict): 包含音频相关元数据的字典，用于构建评估提示
            lyrics_payload (dict): 包含歌词相关元数据的字典，用于构建评估提示
    
        返回值:
            LlmMatchResult | None: 返回包含评分和原因的匹配结果对象，如果功能未启用或发生错误则返回None
        """
        # 检查评估功能是否在配置中启用
        if not self.cfg.enabled:
            return None

        # 构建发送给LLM的提示结构，包含任务类型、约束条件和评估数据
        prompt = {
            "task": "score_audio_lyrics_match",  # 任务类型标识
            "constraints": [
                "return strict json only",  # 约束条件：仅返回严格JSON格式
                "score in [0,1]",           # 约束条件：评分范围在0到1之间
                "reason should be short and factual",  # 约束条件：原因简短且基于事实
            ],
            "audio": audio_payload,  # 音频元数据
            "lyrics": lyrics_payload,  # 歌词元数据
        }

        try:
            # 向LLM服务端点发送POST请求进行评分
            response = requests.post(
                self.cfg.endpoint,  # 从配置获取API端点
                timeout=self.cfg.timeout_sec,  # 设置请求超时时间
                headers={"Content-Type": "application/json"},  # 设置请求头为JSON格式
                json={
                    "model": self.cfg.model,  # 使用配置的模型
                    "temperature": 0.0,  # 设置temperature为0以获得确定性输出
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a strict scoring engine for music metadata and lyrics matching. "
                                "Output json only: {\"score\":number,\"reason\":string}."  # 系统消息：指导LLM作为评分引擎
                            ),
                        },
                        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},  # 用户消息：将提示转换为JSON字符串
                    ],
                },
            )
            response.raise_for_status()  # 检查HTTP响应状态，如有错误则抛出异常
            payload = response.json()  # 解析JSON响应
            content = payload["choices"][0]["message"]["content"]  # 提取LLM回复的内容
            parsed = json.loads(content)  # 将内容字符串解析为Python字典
            score = float(parsed.get("score", 0.0))  # 提取评分，若缺失则默认为0.0并转换为浮点数
            score = max(0.0, min(1.0, score))  # 确保评分在0.0到1.0的范围内
            reason = str(parsed.get("reason", "llm_scored"))  # 提取原因，若缺失则使用默认值并转换为字符串
            return LlmMatchResult(score=score, reason=reason)  # 创建并返回匹配结果对象
        except Exception:
            return None  # 如果发生任何异常（如网络错误、JSON解析错误等），返回None
