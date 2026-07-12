"""单条岗位匹配的核心逻辑（P1#7 两档 / P2#14 重试共用）。

把「加载岗位画像 → 硬条件预筛 → 缓存命中 → LLM 匹配」封装成无状态函数，
workflow 的两阶段（quick 全量 / deep Top-K）与重试接口都调用它，避免逻辑重复、保证一致。
"""
from __future__ import annotations

from typing import NamedTuple

from config import get_settings
from database import SessionLocal
from models import Job, JobAnalysis, MatchResult, Resume
from schemas.job import JobProfile
from schemas.match import MatchResultModel
from schemas.resume import ResumeProfile
from services import match_agent
from services.precheck import precheck_job


class MatchOutcome(NamedTuple):
    match: MatchResultModel | None
    key: str
    cache_hit: bool
    error: str | None
    hard_fail: bool


def _load_job_profile(jid: int):
    """加载岗位结构化画像；缺失时用 jobs 主表兜底造一个最小画像。"""
    db = SessionLocal()
    try:
        job = db.get(Job, jid)
        if job is None:
            return None, None
        ja = db.query(JobAnalysis).filter(JobAnalysis.job_id == jid).first()
        if ja and ja.analysis_json:
            return job, JobProfile.model_validate(ja.analysis_json)
        return job, JobProfile(
            company_name=job.company_name or "",
            job_title=job.job_title or "",
            city=job.city or "",
            salary=job.salary or "",
        )
    finally:
        db.close()


def run_single_match(
    resume_profile: dict,
    resume_id: int,
    jid: int,
    *,
    tier: str = "deep",
    prompt_version: str = match_agent.PROMPT_VERSION,
) -> MatchOutcome:
    """对单个岗位跑一档匹配（tier=quick 用快速模型，tier=deep 用推理模型）。

    - 硬条件预筛不通过 → 直接返回 0/D（hard_fail=True），不调 LLM
    - 命中缓存 → 直接复用缓存结果（cache_hit=True），不调 LLM
    - 否则调用 match_agent.run，返回结果 + 缓存键
    """
    settings = get_settings()
    resume = ResumeProfile.model_validate(resume_profile)
    job, job_profile = _load_job_profile(jid)
    if job is None or job_profile is None:
        return MatchOutcome(None, "", False, f"岗位 {jid} 不存在", False)

    analyze_mode = job.analyze_mode or "summary"
    model = (
        settings.llm_fast_model or settings.llm_model
        if tier == "quick"
        else settings.llm_reasoning_model or settings.llm_model
    )
    model_role = "fast" if tier == "quick" else "reasoning"
    key = match_agent.build_match_cache_key(
        resume.model_dump(), job_profile.model_dump(), model, analyze_mode, prompt_version
    )

    # 硬条件预筛（规则，不消耗 LLM）
    pre = precheck_job(resume, job_profile)
    if not pre["passed"]:
        return MatchOutcome(
            match_agent.build_hard_fail_result(pre["hard_failures"], job_profile),
            key,
            False,
            None,
            True,
        )

    # 缓存命中：相同 简历+岗位+模型+模式+Prompt版本 时跳过 LLM
    db = SessionLocal()
    try:
        row = (
            db.query(MatchResult)
            .filter(MatchResult.cache_key == key)
            .order_by(MatchResult.id.desc())
            .first()
        )
        if row and row.detail_json:
            return MatchOutcome(
                MatchResultModel.model_validate(row.detail_json), key, True, None, False
            )
    finally:
        db.close()

    # 深度 + full 模式才额外传简历原文（token 翻倍，仅 Top-K 值得）
    resume_text: str | None = None
    if tier == "deep" and analyze_mode == "full":
        rdb = SessionLocal()
        try:
            rrow = rdb.get(Resume, resume_id)
            if rrow:
                resume_text = rrow.raw_text
        finally:
            rdb.close()

    try:
        match = match_agent.run(
            resume, job_profile, resume_text=resume_text, model_role=model_role
        )
    except Exception as e:  # noqa: BLE001
        return MatchOutcome(None, key, False, str(e), False)

    return MatchOutcome(match, key, False, None, False)


def persist_match_row(
    task_id: str,
    resume_id: int,
    jid: int,
    match,
    key: str,
    cache_hit: bool,
    *,
    match_mode: str = "deep",
    status: str = "success",
    error: str = "",
) -> int | None:
    """把一条匹配结果写入 match_results（存在则更新、不存在则新建）。

    失败（match=None）时仅落 status=Failed + error_message，方便前端展示并单条重试。
    返回行 id。供 workflow 两档匹配与重试接口复用。
    """
    db = SessionLocal()
    try:
        row = (
            db.query(MatchResult)
            .filter(MatchResult.task_id == task_id, MatchResult.job_id == jid)
            .first()
        )
        if row is None:
            row = MatchResult(resume_id=resume_id, job_id=jid, task_id=task_id)
            db.add(row)
        row.resume_id = resume_id
        row.task_id = task_id
        row.match_mode = match_mode
        row.status = status
        row.error_message = error
        row.cache_key = key
        row.cache_hit = cache_hit
        if match is not None:
            row.score = match.score
            row.level = match.level
            row.matched_points = match.matched_points
            row.missing_points = match.missing_points
            row.recommendation = match.recommendation
            row.risk_notes = match.risk_notes
            row.detail_json = match.model_dump()
        db.commit()
        return row.id
    finally:
        db.close()
