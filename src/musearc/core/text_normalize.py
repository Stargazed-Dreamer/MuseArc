from __future__ import annotations

import re
import unicodedata

_WEAK_CHARS = {
    "∅": "0",
    "Ø": "0",
}


def normalize_text(value: str | None) -> str:
    """归一化输入文本，清理特殊字符并标准化格式。

    参数:
        value (str | None): 输入的字符串，可能为None。

    返回:
        str: 归一化后的字符串。
    """
    # 如果输入为None或空字符串，直接返回空字符串
    if not value:
        return ""
    # 使用_WEAK_CHARS字典替换字符，如果没有替换则保留原字符
    fixed = "".join(_WEAK_CHARS.get(ch, ch) for ch in value)
    # 进行Unicode NFKC标准化，统一字符表示
    fixed = unicodedata.normalize("NFKC", fixed)
    # 使用casefold进行大小写折叠，用于不区分大小写的比较
    fixed = fixed.casefold()
    # 移除方括号及其内容
    fixed = re.sub(r"\[[^\]]*\]", " ", fixed)
    # 移除圆括号及其内容
    fixed = re.sub(r"\([^)]*\)", " ", fixed)
    # 移除非单词字符，保留字母、数字、下划线和中文字符
    fixed = re.sub(r"[^\w\u4e00-\u9fff]+", " ", fixed, flags=re.UNICODE)
    # 压缩多个空格为一个，并去除首尾空格
    return re.sub(r"\s+", " ", fixed).strip()


def token_set(value: str | None) -> set[str]:
    """将输入字符串值转换为一个token集合。

    该函数首先对输入字符串进行规范化处理，然后将其分割为单词并返回一个集合。
    如果输入为None或规范化后为空，则返回空集合。

    参数:
        value (str | None): 要处理的字符串，或None。

    返回值:
        set[str]: 包含规范化后单词的集合。
    """
    norm = normalize_text(value)  # 规范化输入文本
    if not norm:  # 检查规范化后是否为空
        return set()  # 返回空集合
    return set(norm.split(" "))  # 分割文本为单词并返回集合


def lrc_visible_lines(text: str, max_lines: int = 10) -> list[str]:
    """
    从LRC文本中提取可见的歌词行。

    参数:
        text: str - 输入的LRC格式文本。
        max_lines: int - 最大返回行数，默认为10。

    返回:
        list[str] - 包含清理后歌词行的列表。
    """
    lines: list[str] = []
    for line in text.splitlines():  # 遍历文本的每一行
        cleaned = re.sub(r"\[[^\]]+\]", "", line).strip()  # 使用正则表达式去除方括号内的内容（如时间戳），并去除首尾空白
        if cleaned:  # 如果清理后的行非空
            lines.append(cleaned)  # 将其添加到结果列表
        if len(lines) >= max_lines:  # 如果已收集到足够行数
            break  # 提前结束循环
    return lines  # 返回包含歌词行的列表
