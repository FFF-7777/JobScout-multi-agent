"""Job Agent：解析岗位 JD -> JobProfile。"""
from __future__ import annotations

import re

import prompts
from schemas.job import JobProfile
from services import llm_service
from services.jd_preprocessor import (
    build_jd_preview,
    clean_ocr_jd,
    clean_web_jd,
    extract_ocr_job_hints,
)

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
    text = (jd_text or "").strip()
    merged_hints = dict(hints or {})

    if source == "ocr_image":
        for key, value in extract_ocr_job_hints(text).items():
            if value and not merged_hints.get(key):
                merged_hints[key] = value
        text = clean_ocr_jd(text)
    elif source == "url":
        text = clean_web_jd(text)

    prefix = ""
    if merged_hints:
        known = {k: v for k, v in merged_hints.items() if v}
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

    for field in ("company_name", "job_title", "city", "salary", "education"):
        if not getattr(profile, field) and merged_hints.get(field):
            setattr(profile, field, merged_hints[field])

    if not profile.internship_duration and merged_hints.get("internship_duration"):
        profile.internship_duration = merged_hints["internship_duration"]
    if not profile.internship_days_per_week and merged_hints.get("internship_days_per_week"):
        profile.internship_days_per_week = _to_int_or_none(
            merged_hints["internship_days_per_week"]
        )

    profile.risk_tags = [t for t in profile.risk_tags if t in _VALID_RISKS]
    if not profile.jd_summary.strip():
        profile.jd_summary = build_jd_preview(profile)
    return profile
