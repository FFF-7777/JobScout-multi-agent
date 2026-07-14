"""深度分析外部研究服务。"""
from __future__ import annotations

import hashlib
import json
from typing import Callable

from pydantic import BaseModel, Field

from config import get_settings
from schemas.job import JobProfile
from services import llm_service
from services.research_router import ResearchPlan


class ResearchContext(BaseModel):
    enabled: bool = False
    status: str = "disabled"
    attempted: bool = False
    queries: list[str] = Field(default_factory=list)
    summary_items: list[str] = Field(default_factory=list)
    source_notes: list[str] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    provider: str = ""
    verifiable: bool = False
    hash: str = ""
    reason: str = ""
    error: str = ""


def empty_context(
    reason: str = "",
    *,
    status: str = "disabled",
    attempted: bool = False,
    queries: list[str] | None = None,
    error: str = "",
) -> ResearchContext:
    return ResearchContext(
        enabled=False,
        status=status,
        attempted=attempted,
        queries=queries or [],
        reason=reason,
        error=error,
        hash=_hash_payload({"status": status, "queries": queries or [], "error": error}),
    )


def _hash_payload(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def fetch_research_context(
    job: JobProfile,
    *,
    plan: ResearchPlan,
    on_event: Callable[[str, dict], None] | None = None,
) -> ResearchContext:
    def emit(phase: str, **payload) -> None:
        if on_event:
            on_event(phase, payload)

    if not plan.enabled:
        emit("research_skipped", reason=plan.reason, queries=plan.queries)
        return empty_context(plan.reason, status="disabled" if plan.strategy == "off" else "skipped")

    settings = get_settings()
    if not settings.role_configured("reasoning"):
        emit("research_skipped", reason="推理模型未配置", queries=plan.queries)
        return empty_context("推理模型未配置，跳过深度研究。", status="skipped", queries=plan.queries)

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
        emit("research_searching", queries=plan.queries)
        data, search_meta = llm_service.chat_json_with_search_metadata(
            system,
            user,
            model_role="reasoning",
            forced_search=plan.strategy == "force",
            search_strategy=plan.strategy,
        )
    except Exception as exc:  # noqa: BLE001
        emit("research_degraded", queries=plan.queries, error=str(exc)[:500])
        return empty_context(
            "联网研究失败，已降级为仅基于简历和岗位信息分析。",
            status="degraded",
            attempted=True,
            queries=plan.queries,
            error=str(exc)[:500],
        )

    summary_items = [str(item).strip() for item in (data.get("summary_items") or []) if str(item).strip()][:6]
    source_notes = [str(item).strip() for item in (data.get("source_notes") or []) if str(item).strip()][:6]
    search_performed = search_meta.get("performed")
    if search_performed is False:
        emit("research_skipped", queries=plan.queries, reason="供应商确认未执行搜索")
        return empty_context(
            "模型响应确认本次未执行搜索，已降级为原始岗位分析。",
            status="skipped",
            attempted=True,
            queries=plan.queries,
        )
    payload = {
        "queries": plan.queries,
        "summary_items": summary_items,
        "source_notes": source_notes,
        "sources": search_meta.get("sources") or [],
    }
    if not summary_items:
        emit("research_degraded", queries=plan.queries, error="未返回可用研究摘要")
        return empty_context(
            "联网请求未返回可用研究摘要，已降级。",
            status="degraded",
            attempted=True,
            queries=plan.queries,
        )
    emit(
        "research_complete",
        queries=plan.queries,
        source_count=len(search_meta.get("sources") or []),
        verifiable=bool(search_meta.get("verifiable")),
    )
    return ResearchContext(
        enabled=True,
        status="success",
        attempted=True,
        queries=plan.queries,
        summary_items=summary_items,
        source_notes=source_notes,
        sources=search_meta.get("sources") or [],
        provider=str(search_meta.get("provider") or ""),
        verifiable=bool(search_meta.get("verifiable")),
        hash=_hash_payload(payload),
        reason=plan.reason,
    )
