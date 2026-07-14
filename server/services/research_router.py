"""决定是否为深度分析补充外部研究。"""
from __future__ import annotations

from pydantic import BaseModel, Field

from config import get_settings
from schemas.job import JobProfile


class ResearchPlan(BaseModel):
    enabled: bool = False
    strategy: str = "off"
    reason: str = ""
    queries: list[str] = Field(default_factory=list)


def _clean(value: str) -> str:
    return (value or "").strip()


def build_research_plan(
    job: JobProfile,
    *,
    tier: str,
    analyze_mode: str,
) -> ResearchPlan:
    settings = get_settings()
    if tier != "deep":
        return ResearchPlan(
            enabled=False,
            strategy="off",
            reason="快速分析不走外部研究链路。",
        )
    strategy = "force"

    required = [skill for skill in (job.required_skills or []) if _clean(skill)]
    preferred = [skill for skill in (job.preferred_skills or []) if _clean(skill)]

    company = _clean(job.company_name)
    title = _clean(job.job_title)
    city = _clean(job.city)

    queries: list[str] = []
    if company and title:
        queries.append(f"{company} {title} 技术栈 招聘")
    if title and city:
        queries.append(f"{title} {city} 岗位要求")
    if required:
        queries.append(f"{title or '该岗位'} {' '.join(required[:4])} 技术要求")
    elif preferred:
        queries.append(f"{title or '该岗位'} {' '.join(preferred[:4])} 加分技能")

    should_enable = analyze_mode == "full" or tier == "deep"
    return ResearchPlan(
        enabled=should_enable and bool(queries),
        strategy=strategy,
        reason="深度分析固定强制联网；联网失败时由深度模型基于已有材料继续分析。" if should_enable and queries else "岗位信息不足以生成联网查询，改由深度模型基于已有材料分析。",
        queries=queries[: max(1, settings.deep_research_max_items)],
    )
