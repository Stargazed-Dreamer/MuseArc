from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np

from .commands import MediaCommandError


@dataclass(slots=True)
class DecodedAudio:
    samples: np.ndarray
    sample_rate: int
    channels: int


def _first_audio_stream(container: av.container.InputContainer):
    """
    从多媒体容器中查找并返回第一个音频流。

    该函数遍历输入容器中的所有流，找到第一个类型为"audio"的流并返回。
    如果没有找到任何音频流，则抛出MediaCommandError异常。

    参数:
        container (av.container.InputContainer): 多媒体容器对象，包含一个或多个媒体流。

    返回:
        av.audio.stream.AudioStream: 找到的第一个音频流对象。

    异常:
        MediaCommandError: 当容器中不存在任何音频流时抛出。
    """
    # 遍历容器中的所有媒体流
    for stream in container.streams:
        # 检查当前流的类型是否为音频
        if stream.type == "audio":
            # 找到音频流，立即返回该流对象
            return stream
    # 如果循环结束仍未找到音频流，抛出异常
    raise MediaCommandError("no_audio_stream")


def _iter_frames(value):
    """
    功能：将输入值转换为一个列表。
    参数：
        value: 输入值，可能是None、列表或其他类型。
    返回值：一个列表。如果value为None，返回空列表；如果value是列表，返回原列表；否则，返回包含value的列表。
    """
    if value is None:
        return []  # 如果值为None，返回空列表
    if isinstance(value, list):
        return value  # 如果值是列表，直接返回原列表
    return [value]  # 否则，将值包装在单元素列表中返回


def _frame_to_mono_array(frame: av.AudioFrame) -> np.ndarray:
    """将音频帧转换为单声道浮点数数组。

    参数:
        frame (av.AudioFrame): 输入的音频帧对象，包含音频数据和通道信息。

    返回:
        np.ndarray: 单声道浮点数数组，形状为 (samples,)。
    """
    # 将音频帧转换为NumPy数组，并转换为float32类型（copy=False尽可能避免复制）
    array = frame.to_ndarray().astype(np.float32, copy=False)
    # 如果是二维数组（多声道数据）
    if array.ndim == 2:
        # 如果只有一个声道（形状为[1, samples]），提取为一维数组
        if array.shape[0] == 1:
            return array[0]
        # 多个声道：按声道维度取平均值，合并为单声道
        return array.mean(axis=0)
    # 已经是单声道一维数组，直接返回
    return array


def decode_audio(
    path: Path,
    *,
    target_rate: int | None = None,
    target_layout: str = "mono",
    apply_loudnorm: bool = False,
    target_lufs: float = -14.0,
) -> DecodedAudio:
    """将音频文件解码为浮点数组，支持重采样、声道转换和可选的响度标准化。
    Args:
        path (Path): 输入音频文件的路径。
        target_rate (int | None, optional): 目标采样率。如果为None，则使用源文件的采样率。默认为None。
        target_layout (str, optional): 目标声道布局，例如"mono"或"stereo"。默认为"mono"。
        apply_loudnorm (bool, optional): 是否应用EBU R128响度标准化。默认为False。
        target_lufs (float, optional): 应用响度标准化时的目标响度（LUFS）。默认为-14.0。
    Returns:
        DecodedAudio: 一个包含以下字段的命名元组：
            - samples (np.ndarray): 解码后的单精度浮点音频样本数组。
            - sample_rate (int): 最终的采样率。
            - channels (int): 最终的声道数。
    """
    try:
        # 以忽略损坏帧和检测错误的模式打开音频文件
        with av.open(str(path), options={"fflags": "+discardcorrupt", "err_detect": "ignore_err"}) as container:
            stream = _first_audio_stream(container)
            # 获取源采样率，如果无法获取则默认使用48000Hz
            source_rate = stream.codec_context.sample_rate or 48000
            # 确定最终使用的采样率：优先使用目标采样率，否则使用源采样率
            rate = int(target_rate or source_rate)
            # 确定最终使用的声道布局
            layout = str(target_layout or "mono")
            chunks: list[np.ndarray] = []  # 用于存储处理后的音频数据块

            # 如果需要应用响度标准化
            if apply_loudnorm:
                # 创建一个滤波器图来处理音频流
                graph = av.filter.Graph()
                # 添加音频缓冲区作为图的输入源，模板复用原始流
                src = graph.add_abuffer(template=stream)
                # 添加EBU R128响度标准化滤波器，设置集成响度、真峰值和响度范围目标
                loud = graph.add("loudnorm", args=f"I={float(target_lufs):.1f}:TP=-1.5:LRA=11")
                # 添加格式转换滤波器，将音频转换为单精度浮点、指定采样率和声道布局
                fmt = graph.add(
                    "aformat",
                    args=f"sample_fmts=fltp:sample_rates={rate}:channel_layouts={layout}",
                )
                # 添加音频缓冲区作为图的输出汇
                sink = graph.add("abuffersink")
                # 按顺序连接滤波器：输入源 -> 响度标准化 -> 格式转换 -> 输出汇
                src.link_to(loud)
                loud.link_to(fmt)
                fmt.link_to(sink)
                graph.configure()  # 配置并初始化滤波器图

                # 遍历原始容器中的数据包（针对目标音频流）
                for packet in container.demux(stream):
                    try:
                        frames = packet.decode()  # 解码数据包为音频帧
                    except Exception:
                        continue  # 跳过解码失败的包
                    for frame in frames:
                        try:
                            src.push(frame)  # 将音频帧推送到滤波器图的输入
                        except Exception:
                            continue  # 跳过推送失败的帧
                        # 持续从滤波器图的输出拉取处理后的帧
                        while True:
                            try:
                                out = sink.pull()
                            except Exception:
                                break  # 拉取失败时跳出内层循环
                            chunks.append(_frame_to_mono_array(out))  # 转换为单声道数组并存储
                # 发送EOF信号，通知滤波器图没有更多输入
                try:
                    src.push(None)
                except Exception:
                    pass  # 忽略推送None时的错误
                # 拉取滤波器图中剩余的缓冲帧
                while True:
                    try:
                        out = sink.pull()
                    except Exception:
                        break  # 拉取失败时结束
                    chunks.append(_frame_to_mono_array(out))
            # 如果不应用响度标准化，则进行简单的重采样和格式转换
            else:
                # 创建一个音频重采样器，输出为单精度浮点、指定布局和采样率
                resampler = av.AudioResampler(format="fltp", layout=layout, rate=rate)
                # 遍历原始容器中的数据包（针对目标音频流）
                for packet in container.demux(stream):
                    try:
                        frames = packet.decode()  # 解码数据包为音频帧
                    except Exception:
                        continue  # 跳过解码失败的包
                    for frame in frames:
                        try:
                            # 对帧进行重采样，并获取一个可能产生多个帧的迭代器
                            resampled_frames = _iter_frames(resampler.resample(frame))
                        except Exception:
                            continue  # 跳过重采样失败的帧
                        for out_frame in resampled_frames:
                            chunks.append(_frame_to_mono_array(out_frame))  # 转换并存储
                # 重采样器刷新，获取残留的样本（传入None表示结束）
                for out_frame in _iter_frames(resampler.resample(None)):
                    chunks.append(_frame_to_mono_array(out_frame))

            # 如果有数据块，则沿时间轴（axis=0）拼接所有数据块；否则创建一个空的浮点数组
            samples = np.concatenate(chunks, axis=0) if chunks else np.zeros(0, dtype=np.float32)
            # 确定最终声道数：如果布局为"mono"则是1，否则尝试从流上下文获取，获取失败则默认为2
            channels = 1 if layout == "mono" else (stream.codec_context.channels or 2)
            return DecodedAudio(samples=samples, sample_rate=rate, channels=channels)
    except MediaCommandError:
        raise  # 重新抛出已有的MediaCommandError，不进行包装
    except Exception as exc:  # pragma: no cover - backend specific
        # 捕获其他所有底层或意外异常，将其包装为MediaCommandError并重新抛出，附带原始异常信息
        raise MediaCommandError(f"decode_failed:{path}:{exc}") from exc

