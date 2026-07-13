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
    "立即申请",
    "收藏职位",
    "举报职位",
    "取消",
    "职位举报",
}

WEB_CUTOFF_MARKERS = (
    "认证资质",
    "营业执照信息",
    "为您推荐更多相似职位",
    "查看更多相似职位",
    "周边城市",
    "最新招聘",
    "热门城市",
    "热门职位",
    "热门公司",
)

WEB_NOISE_LINE_PATTERNS = (
    r"^立即申请$",
    r"^收藏职位$",
    r"^举报职位$",
    r"^取消$",
    r"^查看更多相似职位$",
)

_COMPANY_ROLE_SEP_RE = re.compile(r"^(?P<company>[^·•]{2,40})[·•](?P<role>.+)$")
_SALARY_RE = re.compile(r"(?P<salary>\d{2,4}\s*[-~～]\s*\d{2,4}\s*元\s*/?\s*[天月])")
_INTERNSHIP_RE = re.compile(r"(?P<days>\d+)\s*天/周\s*(?P<duration>\d+)\s*个?月")
_EDUCATION_RE = re.compile(r"(大专|本科|硕士|博士|学历不限)")
_ACTIVE_RE = re.compile(r"(刚刚|今日|本周内|近[0-9一二三四五六七八九十]+天|[0-9一二三四五六七八九十]+月内)活跃")
_COMPANY_ROLE_HINTS = (
    "招聘者",
    "hr",
    "hrbp",
    "猎头",
    "经理",
    "总监",
    "主管",
    "负责人",
    "面试官",
    "创始人",
    "技术",
    "开发",
    "人力",
    "行政",
    "产品",
    "运营",
)


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


def clean_web_jd(raw_text: str) -> str:
    """清洗链接抓取的 JD 文本。

    目标：
    - 保留岗位标题 / 薪资 / 公司 / 城市 / 职责 / 要求等正文事实
    - 截断智联等招聘站页面尾部的推荐职位、热门城市、热门公司等模板噪声
    - 删除页面交互按钮类文本，减少对公司名、薪资、岗位名的污染
    """
    text = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    # 1) 尽量把常见板块标题恢复成单独行，便于后续规则截断与下游 LLM 理解
    text = re.sub(
        r"(工作地址|职位描述|岗位职责[:：]?|任职要求[:：]?|岗位要求[:：]?|职位福利|认证资质|营业执照信息|为您推荐更多相似职位|查看更多相似职位|周边城市|最新招聘|热门城市|热门职位|热门公司)",
        r"\n\1\n",
        text,
    )

    # 2) 清理多余空白
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 3) 逐行清洗，并在强噪声区块起点处直接截断
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in NOISE_LINES:
            continue
        if any(marker in line for marker in WEB_CUTOFF_MARKERS):
            break
        if any(re.search(pattern, line) for pattern in WEB_NOISE_LINE_PATTERNS):
            continue
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)

    # 4) 压缩尾部常见站点免责声明后面的残留空行
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def extract_ocr_job_hints(raw_text: str) -> dict[str, str]:
    """从 OCR JD 原文中用规则提取高确定性字段，给下游 LLM 做 hints。"""
    text = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    hints: dict[str, str] = {}

    if lines:
        first = lines[0]
        if 2 <= len(first) <= 40 and not _SALARY_RE.search(first) and "职位描述" not in first:
            hints["job_title"] = first

    for line in lines[:8]:
        if not hints.get("salary"):
            m = _SALARY_RE.search(line)
            if m:
                hints["salary"] = re.sub(r"\s+", "", m.group("salary"))
        if not hints.get("education"):
            m = _EDUCATION_RE.search(line)
            if m:
                hints["education"] = m.group(1)
        if not hints.get("internship_duration"):
            m = _INTERNSHIP_RE.search(line)
            if m:
                hints["internship_duration"] = f"{m.group('duration')}个月"
                hints["internship_days_per_week"] = m.group("days")

    city_idx = next((i for i, line in enumerate(lines[:10]) if line == "深圳"), None)
    if city_idx is not None:
        hints["city"] = lines[city_idx]
    else:
        for line in lines[:10]:
            if line in {"北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安", "南京", "苏州"}:
                hints["city"] = line
                break

    for line in reversed(lines[-8:]):
        if _ACTIVE_RE.search(line):
            continue
        if "与BOSS随时沟通" in line or "去App" in line or line in NOISE_LINES:
            continue
        match = _COMPANY_ROLE_SEP_RE.match(line)
        if not match:
            continue
        company = match.group("company").strip()
        role = match.group("role").strip().lower()
        if not company or len(company) < 2:
            continue
        if any(token in role for token in _COMPANY_ROLE_HINTS):
            hints["company_name"] = company
            break

    return hints


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
