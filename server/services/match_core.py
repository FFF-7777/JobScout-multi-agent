"""Core matching flow for a single job item."""
from __future__ import annotations

import hashlib

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
    """Load the structured job profile, falling back to minimal job fields when missing."""
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
    """Run one match request for a single job. quick uses the fast model, deep uses the reasoning model."""
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

    # 简历原文也作为匹配主输入参与所有档位分析，减少仅靠结构化画像带来的细节丢失。
    # tier / resume_text_hash / policy_version 一并纳入缓存键，避免不同档位或不同原文版本串缓存。
    resume_text: str | None = None
    rdb = SessionLocal()
    try:
        rrow = rdb.get(Resume, resume_id)
        if rrow:
            resume_text = rrow.raw_text
    finally:
        rdb.close()
    resume_text_hash = hashlib.sha256((resume_text or "").encode("utf-8")).hexdigest()[:32]

    key = match_agent.build_match_cache_key(
        resume.model_dump(),
        job_profile.model_dump(),
        model,
        analyze_mode,
        prompt_version,
        tier=tier,
        resume_text_hash=resume_text_hash,
        policy_version=match_agent.MATCH_POLICY_VERSION,
    )

    # 纭潯浠堕绛涳紙瑙勫垯锛屼笉娑堣€?LLM锛?    pre = precheck_job(resume, job_profile)
    hard_items: list[dict] = pre.get("items", [])
    if not pre["passed"]:
        return MatchOutcome(
            match_agent.build_hard_fail_result(
                pre["hard_failures"], job_profile, hard_items=[match_agent.HardConditionItem(**h) for h in hard_items]
            ),
            key,
            False,
            None,
            True,
        )

    # 缂撳瓨鍛戒腑锛氱浉鍚?绠€鍘?宀椾綅+妯″瀷+妯″紡+Prompt鐗堟湰 鏃惰烦杩?LLM
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

    try:
        match = match_agent.run(
            resume, job_profile, resume_text=resume_text, model_role=model_role, hard_conditions=hard_items,
        )
    except Exception as e:  # noqa: BLE001
        return MatchOutcome(None, key, False, str(e), False)

    return MatchOutcome(match, key, False, None, False)


def persist_match_row(
    task_id: str | None,
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
    """Persist one match result row for the current task and job."""
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
            try:
                db.flush()  # trigger insert early so uniqueness errors surface sooner
            except Exception:
                db.rollback()
                row = (
                    db.query(MatchResult)
                    .filter(MatchResult.task_id == task_id, MatchResult.job_id == jid)
                    .first()
                )
                if row is None:
                    raise  # 鐪熷紓甯革紝鍚戜笂浼犳挱
        row.resume_id = resume_id
        row.task_id = task_id
        row.match_mode = match_mode
        row.status = status
        row.error_message = error
        row.cache_key = key
        row.cache_hit = cache_hit
        if match is not None:
            if match_mode == "deep":
                # deep 鎴愬姛锛氫繚鐣?quick 鍒嗘暟锛岃褰?deep 鍒嗘暟锛屾渶缁堝垎鏁扮敤 deep 瑕嗙洊
                row.quick_score = row.quick_score or row.score
                row.deep_score = match.score
                row.partial_success = False
                row.deep_error_message = ""
            else:
                # quick 鎴愬姛锛氳褰?quick 鍒嗘暟
                row.quick_score = match.score
                row.partial_success = False
                row.deep_error_message = ""
            row.score = match.score
            row.level = match.level
            row.matched_points = match.matched_points
            row.missing_points = match.missing_points
            row.recommendation = match.recommendation
            row.risk_notes = match.risk_notes
            row.detail_json = match.model_dump()
        else:
            # deep 失败只有在存在 quick 兜底时才算 partial；直接 deep 的失败仍然是 failed
            if match_mode == "deep" and status == "partial":
                row.partial_success = True
                row.deep_error_message = error
                # 保留已有 quick_score / score / level 不变
            else:
                row.partial_success = False
        db.commit()
        return row.id
    finally:
        db.close()
