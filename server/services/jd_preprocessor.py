"""JD 规则清洗器（非 Agent）：便宜、确定性的文本预处理。

职责边界（重要）：
- 这一层只做「去噪 / 合并断行 / 修复固定格式 / 规范章节标题」，用正则和代码即可，
  不消耗 Token、结果稳定，不会像 LLM 那样误删有效信息或产生内容漂移。
- 语义理解与结构化提取交给下游 Job Agent（LLM）。两者职责互补，不重叠。

主要用于招聘网站截图 OCR 出来的原始文本（BOSS 直聘 / 拉勾 / 智联等）。
"""
from __future__ import annotations

import re

# 招聘页面里与 JD 正文无关的固定噪声行（整行完全匹配才删，避免误伤正文）
NOISE_LINES = {
    "收藏",
    "立即沟通",
    "微信扫码分享",
    "举报",
    "去App",
    "去 App",
    "与BOSS随时沟通",
    "在线",
    "分享",
    "投递",
    "已投递",
    "求职者",
}


def clean_ocr_jd(raw_text: str) -> str:
    """清洗 OCR 出来的 JD 原始文本，返回更干净的正文。

    只做确定性处理，绝不改动薪资 / 城市 / 学历 / 实习天数 / 毕业年份等关键事实。
    """
    text = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n")

    # 1) 修复上下文明确的常见 OCR 错误（不做全局替换所有 Al，避免误伤）
    text = re.sub(r"\bA[lI1]\s*Agent\b", "AI Agent", text, flags=re.IGNORECASE)
    text = re.sub(r"\bA[lI1]\s*应用\b", "AI 应用", text, flags=re.IGNORECASE)
    text = re.sub(r"\bA[lI1]\s*大模型\b", "AI 大模型", text, flags=re.IGNORECASE)

    # 2) 修复因换行被强制断开的常见技术词
    text = re.sub(r"Prom\s*\n\s*pt", "Prompt", text, flags=re.IGNORECASE)
    text = re.sub(r"Lang\s*\n\s*Chain", "LangChain", text, flags=re.IGNORECASE)
    text = re.sub(r"Auto\s*\n\s*Gen", "AutoGen", text, flags=re.IGNORECASE)
    text = re.sub(r"Py\s*\n\s*thon", "Python", text, flags=re.IGNORECASE)

    # 3) 逐行过滤噪声
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in NOISE_LINES:
            continue
        # BOSS 页面底部：招聘者活跃状态，如「3周内活跃」「本月活跃」
        if re.search(r"\d+\s*(周|天|月|小时|分钟)内活跃$", line):
            continue
        if re.search(r"^(本月|今日|刚刚|近期)活跃$", line):
            continue
        # 招聘者名片行：如「张经理·招聘者」
        if line.endswith("·招聘者") or line.endswith("· 招聘者"):
            continue
        if "随时沟通" in line:
            continue
        # 纯图标/装饰性极短行（1 个非中英文数字字符）
        if len(line) == 1 and not re.match(r"[\u4e00-\u9fffA-Za-z0-9]", line):
            continue
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)

    # 4) 合并「一个词被 OCR 在中英文字符间硬断开」的情况
    text = re.sub(
        r"([\u4e00-\u9fffA-Za-z])\n([\u4e00-\u9fffA-Za-z])",
        r"\1\2",
        text,
    )

    # 5) 重新保证章节标题独占一行，便于下游解析
    text = re.sub(
        r"(\[岗位职责\]|\[职位要求\]|\[任职要求\]|【岗位职责】|【职位要求】|【任职要求】)",
        r"\n\1\n",
        text,
    )

    # 6) 压缩多余空行
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def build_jd_preview(profile, max_length: int = 160) -> str:
    """根据结构化字段拼接列表预览（确定性、不额外调用 LLM）。

    优先用 LLM 产出的 jd_summary；没有时用职责 + 核心技能拼一段简洁预览。
    事实来源是结构化字段，预览只是它们的显示结果，不会改写事实。
    """
    summary = (getattr(profile, "jd_summary", "") or "").strip()
    if summary:
        return summary[:max_length]

    parts: list[str] = []
    responsibilities = getattr(profile, "responsibilities", None) or []
    required_skills = getattr(profile, "required_skills", None) or []
    if responsibilities:
        parts.extend(responsibilities[:2])
    if required_skills:
        skills = "、".join(required_skills[:5])
        parts.append(f"核心要求：{skills}")

    preview = "；".join(p.strip() for p in parts if p and p.strip())
    return preview[:max_length]


def assess_ocr_quality(text: str) -> float:
    """给 OCR 文本打一个 0-1 的质量分，用于判断是否需要多模态兜底。

    低分（如 < 0.65）意味着排版复杂 / 乱码多 / 几乎没识别出内容，
    此时才有必要调用更贵的多模态视觉模型直接解析图片。
    """
    text = text or ""
    score = 0.0

    if len(text) >= 200:
        score += 0.25

    keywords = ["岗位职责", "职位要求", "任职要求", "薪资", "学历", "Python"]
    score += min(sum(kw in text for kw in keywords) * 0.1, 0.4)

    weird_count = len(
        re.findall(r"[^\u4e00-\u9fffA-Za-z0-9，。；：、（）()\[\]\-+/.\n ]", text)
    )
    weird_ratio = weird_count / max(len(text), 1)
    if weird_ratio < 0.03:
        score += 0.2

    if "岗位职责" in text or "职位要求" in text or "任职要求" in text:
        score += 0.15

    return min(score, 1.0)
