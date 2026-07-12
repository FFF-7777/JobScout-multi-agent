"""Job Agent：解析岗位 JD -> JobProfile。"""
from __future__ import annotations

import prompts
from schemas.job import JobProfile
from services import llm_service

_VALID_RISKS = {"外包", "培训", "销售", "运营", "助教", "不相关"}


def run(jd_text: str, hints: dict | None = None) -> JobProfile:
    """hints 可包含来自表格的已知字段（company_name/city/salary 等）。"""
    text = (jd_text or "").strip()
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
    )
    profile = JobProfile.model_validate(data)
    # 用已知字段覆盖空值
    if hints:
        for field in ("company_name", "job_title", "city", "salary"):
            if not getattr(profile, field) and hints.get(field):
                setattr(profile, field, hints[field])
    # 过滤非法风险标签
    profile.risk_tags = [t for t in profile.risk_tags if t in _VALID_RISKS]
    return profile
