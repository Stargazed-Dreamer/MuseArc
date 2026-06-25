from __future__ import annotations

import re


def first_letter(text: str) -> str:
    """获取字符串的首字母。
    
    处理逻辑如下：
    1. 若输入为空或处理后为空字符串，返回"#"。
    2. 若首字符是英文字母，直接返回其大写形式。
    3. 若首字符是中文等非英文字母，尝试使用拼音库获取其拼音的首字母并大写返回。
    4. 若以上所有尝试均失败，返回"#"。

    Args:
        text (str): 需要提取首字母的输入文本。

    Returns:
        str: 计算得到的首字母（大写），若无法获取则返回"#"。
    """
    # 处理输入：如果 text 为 None 或空字符串，则使用空字符串，再去除首尾空白字符
    value = (text or "").strip()
    # 如果处理后的字符串为空，则直接返回占位符"#"
    if not value:
        return "#"

    ch = value[0]
    # 如果首字符是大写英文字母，则直接返回
    if "A" <= ch <= "Z":
        return ch
    # 如果首字符是小写英文字母，则将其转为大写后返回
    if "a" <= ch <= "z":
        return ch.upper()

    # 对于非英文字母（如中文字符），尝试获取其拼音首字母
    try:
        # 动态导入 pypinyin 库，避免在不需要时加载
        from pypinyin import lazy_pinyin  # type: ignore

        # 获取字符的拼音，并拼接成字符串
        py = "".join(lazy_pinyin(ch))
        # 检查拼音结果是否有效，并且拼音的首字符是英文字母
        if py and re.match(r"[a-zA-Z]", py[0]):
            # 返回拼音首字母的大写形式
            return py[0].upper()
    # 捕获所有可能的异常（如库未安装、导入失败等），静默处理
    except Exception:
        pass

    # 当以上所有条件都不满足时，返回占位符"#"
    return "#"
