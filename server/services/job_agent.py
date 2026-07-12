"""Job Agent：解析岗位 JD -> JobProfile。"""
from __future__ import annotations

import re

import prompts
from schemas.job import JobProfile
from services import llm_service
from services.jd_preprocessor import build_jd_preview, clean_ocr_jd

_VALID_RISKS = {"外包", "培训", "销售", "运营", "助教", "不相关"}

_STR_FIELDS = (
    "company_name",
    "job_title",
    "city",
    "salary",
    "education",
    "experience",
    "job_type",
    "internship_duration",
    "jd_summary",
)
_LIST_FIELDS = (
    "required_skills",
    "preferred_skills",
    "responsibilities",
    "requirements",
    "risk_tags",
)


def _to_int_or_none(v: object) -> int | None:
    """把 LLM 返回的每周实习天数收敛为 int 或 None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"\d+", v)
        return int(m.group()) if m else None
    return None


def _to_int_list(v: object) -> list[int]:
    """把毕业年份收敛为 int 列表（过滤非数字、去重保序）。"""
    if not isinstance(v, list):
        v = [v] if v is not None else []
    out: list[int] = []
    for x in v:
        if isinstance(x, bool):
            continue
        if isinstance(x, int):
            n = x
        elif isinstance(x, float):
            n = int(x)
        elif isinstance(x, str):
            m = re.search(r"\d{4}", x)
            if not m:
                continue
            n = int(m.group())
        else:
            continue
        if n not in out:
            out.append(n)
    return out


def _sanitize_profile(data: object) -> dict:
    """把 LLM 可能返回的脏数据收敛成 JobProfile 期望的类型。

    - 字符串字段：显式 null / 数字 / 缺失 → 空串（Pydantic 默认只在缺键时用默认值，
      显式 null 会直接抛 ValidationError，这是 /analyze 偶发 500 的根因）。
    - 列表字段：非列表 → 空列表；单字符串 → 包成单元素列表；列表内非字符串 → 转字符串。
    """
    if not isinstance(data, dict):
        return {}
    out = dict(data)
    for f in _STR_FIELDS:
        v = out.get(f)
        out[f] = v if isinstance(v, str) else ""
    for f in _LIST_FIELDS:
        v = out.get(f)
        if isinstance(v, list):
            out[f] = [str(x) for x in v if x is not None]
        elif isinstance(v, str):
            out[f] = [v]
        else:
            out[f] = []
    # 实习相关数值字段
    out["internship_days_per_week"] = _to_int_or_none(out.get("internship_days_per_week"))
    out["graduation_years"] = _to_int_list(out.get("graduation_years"))
    return out


def run(
    jd_text: str,
    hints: dict | None = None,
    source: str = "",
    *,
    model_role: str = "fast",
) -> JobProfile:
    """hints 可包含来自表格的已知字段（company_name/city/salary 等）。

    source == "ocr_image" 时，先用规则清洗器去噪，再交给 LLM 结构化。
    """
    text = (jd_text or "").strip()
    if source == "ocr_image":
        text = clean_ocr_jd(text)
    prefix = ""
    if hints:
        known = {k: v for k, v in hints.items() if v}
        if known:
            prefix = "已知字段：" + "；".join(f"{k}={v}" for k, v in known.items()) + "\n\n"
    if not text and not prefix:
        return JobProfile()

    data = llm_service.chat_json(
        prompts.JOB_SYSTEM,
        prompts.JOB_USER.format(jd_text=(prefix + text)[:8000]),
        model_role=model_role,
    )
    data = _sanitize_profile(data)
    profile = JobProfile.model_validate(data)
    # 用已知字段覆盖空值
    if hints:
        for field in ("company_name", "job_title", "city", "salary"):
            if not getattr(profile, field) and hints.get(field):
                setattr(profile, field, hints[field])
    # 过滤非法风险标签
    profile.risk_tags = [t for t in profile.risk_tags if t in _VALID_RISKS]
    # jd_summary 兜底：LLM 没给摘要时用结构化字段拼一段预览
    if not profile.jd_summary.strip():
        profile.jd_summary = build_jd_preview(profile)
    return profile
