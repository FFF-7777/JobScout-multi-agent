"""深度分析外部研究服务。"""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, Field

from config import get_settings
from schemas.job import JobProfile
from services import llm_service
from services.research_router import ResearchPlan


class ResearchContext(BaseModel):
    enabled: bool = False
    queries: list[str] = Field(default_factory=list)
    summary_items: list[str] = Field(default_factory=list)
    source_notes: list[str] = Field(default_factory=list)
    hash: str = ""
    reason: str = ""


def empty_context(reason: str = "") -> ResearchContext:
    return ResearchContext(enabled=False, reason=reason, hash=_hash_payload([]))


def _hash_payload(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def fetch_research_context(
    job: JobProfile,
    *,
    plan: ResearchPlan,
) -> ResearchContext:
    if not plan.enabled:
        return empty_context(plan.reason)

    settings = get_settings()
    if not settings.role_configured("reasoning"):
        return empty_context("推理模型未配置，跳过深度研究。")

    system = (
        "你是技术招聘研究助手。基于给定岗位画像和检索查询，"
        "给出与岗位技术语境强相关的研究摘要。"
        "不要重复岗位原文；只输出 JSON。"
    )
    user = json.dumps(
        {
            "job_profile": job.model_dump(),
            "queries": plan.queries,
            "goal": "总结该岗位在技术栈、工程要求、常见评估重点上的外部语境，只保留可帮助深度分析的内容。",
            "output_schema": {
                "summary_items": ["3-6 条研究摘要"],
                "source_notes": ["简短来源说明，可为空"],
            },
        },
        ensure_ascii=False,
    )
    try:
        data = llm_service.chat_json(
            system,
            user,
            model_role="reasoning",
            enable_search=True,
            forced_search=plan.strategy == "force",
            search_strategy=plan.strategy,
        )
    except Exception as exc:  # noqa: BLE001
        return empty_context(f"深度研究降级：{exc}")

    summary_items = [str(item).strip() for item in (data.get("summary_items") or []) if str(item).strip()][:6]
    source_notes = [str(item).strip() for item in (data.get("source_notes") or []) if str(item).strip()][:6]
    payload = {
        "queries": plan.queries,
        "summary_items": summary_items,
        "source_notes": source_notes,
    }
    return ResearchContext(
        enabled=bool(summary_items),
        queries=plan.queries,
        summary_items=summary_items,
        source_notes=source_notes,
        hash=_hash_payload(payload),
        reason=plan.reason,
    )
