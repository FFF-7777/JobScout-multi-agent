"""Match Agent：对比简历画像与岗位画像，输出匹配评分与证据。"""
from __future__ import annotations

import hashlib
import json

import prompts
from schemas.job import JobProfile
from schemas.match import (
    ApplicationDecision,
    CareerAlignment,
    DimensionScores,
    GapItem,
    HardConditionItem,
    HardConditionResult,
    HrScreening,
    MatchResultModel,
    StrengthItem,
)
from schemas.resume import ResumeProfile
from services import application_policy, llm_service, tech_matcher

PROMPT_VERSION = "4"
MATCH_POLICY_VERSION = "4"

WEIGHTS = {
    "tech_stack": 0.30,
    "project_exp": 0.30,
    "role_direction": 0.20,
    "qualification": 0.10,
    "logistics": 0.10,
}


def rule_tech_coverage(resume: ResumeProfile, job: JobProfile) -> float:
    """规则化技术覆盖率（0-100）。"""
    items, _summary = tech_matcher.build_skill_evidence(resume, job)
    return tech_matcher.coverage_score(items)


def _level_of(score: float) -> str:
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    return "D"


def build_match_cache_key(
    resume_profile: dict,
    job_profile: dict,
    model: str,
    mode: str,
    prompt_version: str = PROMPT_VERSION,
    *,
    tier: str = "deep",
    resume_text_hash: str = "",
    research_hash: str = "",
    policy_version: str = MATCH_POLICY_VERSION,
) -> str:
    payload = {
        "resume": resume_profile,
        "job": job_profile,
        "model": model,
        "mode": mode,
        "tier": tier,
        "resume_text_hash": resume_text_hash,
        "research_hash": research_hash,
        "prompt_version": prompt_version,
        "policy_version": policy_version,
        "weights": WEIGHTS,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _clamp_score(value: object) -> float:
    try:
        x = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if x != x:
        return 0.0
    return max(0.0, min(100.0, x))


def _fallback_requirement_summary(job: JobProfile) -> list[str]:
    parts: list[str] = []
    if job.required_skills:
        parts.append(f"必备技能：{'、'.join(job.required_skills[:6])}")
    if job.preferred_skills:
        parts.append(f"加分项：{'、'.join(job.preferred_skills[:6])}")
    if job.experience:
        parts.append(f"经验要求：{job.experience}")
    if job.education:
        parts.append(f"学历要求：{job.education}")
    return parts[:4]


def _fallback_top_strengths(
    skill_evidence,
    transferable_strengths: list[str],
) -> list[StrengthItem]:
    strengths: list[StrengthItem] = []
    for item in skill_evidence:
        if item.bucket != "confirmed":
            continue
        strengths.append(
            StrengthItem(
                title=f"具备 {item.skill} 直接证据",
                resume_evidence=item.resume_evidence,
                job_relevance=f"岗位明确要求 {item.job_requirement}",
            )
        )
    for text in transferable_strengths:
        if len(strengths) >= 3:
            break
        strengths.append(
            StrengthItem(
                title=text,
                resume_evidence="来自简历中的相近技术或项目经验",
                job_relevance="可作为面试或投递时的可迁移说明",
            )
        )
    return strengths[:3]


def _fallback_main_gaps(skill_evidence) -> list[GapItem]:
    gaps: list[GapItem] = []
    for item in skill_evidence:
        if item.bucket == "not_shown" and item.source == "required":
            gaps.append(
                GapItem(
                    title=f"缺少 {item.skill} 直接证据",
                    severity="major",
                    impact=f"岗位把 {item.job_requirement} 作为明确要求，当前简历未体现。",
                    short_term_fixable=True,
                    action=f"在投递前补充与 {item.skill} 相关的项目、课程或实践证据。",
                )
            )
        elif item.bucket == "partial" and item.source == "required":
            gaps.append(
                GapItem(
                    title=f"{item.skill} 证据不够直接",
                    severity="minor",
                    impact=f"已有相近经验，但不是岗位点名要求的同一技术。",
                    short_term_fixable=True,
                    action=f"在简历或面试表达中，把 {item.resume_evidence} 与 {item.skill} 的迁移关系讲清楚。",
                )
            )
    return gaps[:3]


def build_hard_fail_result(
    hard_failures: list[str],
    job: JobProfile,
    hard_items: list[HardConditionItem] | None = None,
) -> MatchResultModel:
    items = hard_items or [HardConditionItem(name=f, status="fail") for f in hard_failures]
    decision = ApplicationDecision(
        action="skip",
        summary="存在硬性条件不满足，当前不建议投递。",
        reasons=hard_failures[:3],
    )
    return MatchResultModel(
        score=0.0,
        level="D",
        dimensions=DimensionScores(),
        matched_points=[],
        missing_points=[f"硬性条件不符：{failure}" for failure in hard_failures],
        recommendation=decision.summary,
        risk_notes=list(hard_failures[:3]),
        core_job_requirements=_fallback_requirement_summary(job),
        hard_condition_result=HardConditionResult(status="fail", items=items),
        application_decision=decision,
        confidence=100.0,
    )


_DEEP_REVIEW_SUFFIX = (
    "\n\n[深度复核模式] 这是深度分析阶段，请更严格核对每个判断。"
    "逐条检查结论是否同时存在对应的岗位要求与简历证据，优先删掉只有关键词重合、没有项目或经历支持的乐观结论。"
    "区分“不会”“未体现”“有相近经验”，不要把未体现直接判定为不会。"
    "联网信息只能补充岗位与公司语境，不能作为候选人能力证据。"
    "若证据不充分，请明确写“当前简历未提供直接证据”。"
)


def run(
    resume: ResumeProfile,
    job: JobProfile,
    *,
    resume_text: str | None = None,
    research_context: dict | None = None,
    model_role: str = "reasoning",
    tier: str = "deep",
    hard_conditions: list[dict] | None = None,
) -> MatchResultModel:
    skill_evidence, skill_summary = tech_matcher.build_skill_evidence(resume, job)

    resume_block = json.dumps(resume.model_dump(), ensure_ascii=False)
    if resume_text:
        resume_block += "\n\n--- 简历原文（用于补充项目细节）---\n" + resume_text[:8000]

    user = prompts.MATCH_USER.format(
        resume_profile=resume_block,
        job_profile=json.dumps(job.model_dump(), ensure_ascii=False),
        rule_skill_evidence=json.dumps(
            {
                "skill_evidence": [item.model_dump() for item in skill_evidence],
                "skill_evidence_summary": skill_summary.model_dump(),
            },
            ensure_ascii=False,
        ),
        research_context=json.dumps(research_context or {}, ensure_ascii=False),
    )
    if tier == "deep":
        user += _DEEP_REVIEW_SUFFIX

    llm_out = llm_service.chat_json(
        prompts.MATCH_SYSTEM,
        user,
        model_role=model_role,
    )

    llm_dims = llm_out.get("dimensions", {}) or {}
    llm_tech = _clamp_score(llm_dims.get("tech_stack", 0))
    rule_tech = tech_matcher.coverage_score(skill_evidence)
    dims = DimensionScores(
        tech_stack=_clamp_score(round(0.6 * rule_tech + 0.4 * llm_tech, 1)),
        project_exp=_clamp_score(llm_dims.get("project_exp", 0)),
        role_direction=_clamp_score(llm_dims.get("role_direction", 0)),
        qualification=_clamp_score(llm_dims.get("qualification", 0)),
        logistics=_clamp_score(llm_dims.get("logistics", 0)),
    )

    score = _clamp_score(
        round(
            dims.tech_stack * WEIGHTS["tech_stack"]
            + dims.project_exp * WEIGHTS["project_exp"]
            + dims.role_direction * WEIGHTS["role_direction"]
            + dims.qualification * WEIGHTS["qualification"]
            + dims.logistics * WEIGHTS["logistics"],
            1,
        )
    )
    level = _level_of(score)

    top_strengths_raw: list[dict] = llm_out.get("top_strengths", []) or []
    main_gaps_raw: list[dict] = llm_out.get("main_gaps", []) or []
    hr_raw: dict = llm_out.get("hr_screening", {}) or {}
    career_raw: dict = llm_out.get("career_alignment", {}) or {}
    transferable_raw: list[str] = llm_out.get("transferable_strengths", []) or []
    core_req_raw: list[str] = llm_out.get("core_job_requirements", []) or []
    research_summary: list[str] = llm_out.get("research_summary", []) or []

    top_strengths = [
        StrengthItem(
            title=item.get("title", ""),
            resume_evidence=item.get("resume_evidence", ""),
            job_relevance=item.get("job_relevance", ""),
        )
        for item in top_strengths_raw
        if item
    ][:3]
    main_gaps = [
        GapItem(
            title=item.get("title", ""),
            severity=item.get("severity", "major"),
            impact=item.get("impact", ""),
            short_term_fixable=bool(item.get("short_term_fixable", False)),
            action=item.get("action", ""),
        )
        for item in main_gaps_raw
        if item
    ][:3]

    if not top_strengths:
        top_strengths = _fallback_top_strengths(skill_evidence, transferable_raw)
    if not main_gaps:
        main_gaps = _fallback_main_gaps(skill_evidence)

    career_alignment = CareerAlignment(
        score=_clamp_score(career_raw.get("score", 50)),
        analysis=career_raw.get("analysis", ""),
    )
    hr_screening = HrScreening(
        likely_result=hr_raw.get("likely_result", "borderline"),
        main_reason=hr_raw.get("main_reason", ""),
    )
    confidence = _clamp_score(llm_out.get("confidence", 70))

    hard_fail = False
    hard_items: list[HardConditionItem] = []
    if hard_conditions:
        for item in hard_conditions:
            status = item.get("status", "unknown")
            if status == "fail":
                hard_fail = True
            hard_items.append(
                HardConditionItem(
                    name=item.get("name", ""),
                    status=status,
                    resume_evidence=item.get("resume_evidence", ""),
                    job_requirement=item.get("job_requirement", ""),
                )
            )
    hard_condition_result = HardConditionResult(
        status="fail" if hard_fail else "pass",
        items=hard_items,
    )

    application_decision = application_policy.decide_application(
        score=score,
        hard_fail=hard_fail,
        career_score=career_alignment.score,
        role_direction_score=dims.role_direction,
        main_gaps=main_gaps,
    )

    matched_points = [item.title for item in top_strengths][:3]
    missing_points = [
        f"[{item.severity.upper()}] {item.title}" + (f"：{item.impact}" if item.impact else "")
        for item in main_gaps
    ][:3]

    risk_notes = []
    if hr_screening.likely_result == "unlikely" and hr_screening.main_reason:
        risk_notes.append(f"HR 初筛风险：{hr_screening.main_reason}")
    risk_notes.extend([item.action for item in main_gaps if item.action][:3])
    risk_notes = risk_notes[:3]

    next_actions = []
    next_actions.extend([item.action for item in main_gaps if item.action])
    if application_decision.action == "priority_apply":
        next_actions.append("优先把最相关项目排在简历前半段，并尽快投递。")
    elif application_decision.action == "selective_apply":
        next_actions.append("先补齐最关键的证据再投，避免直接裸投。")
    elif application_decision.action == "skip":
        next_actions.append("把时间优先投入到方向更一致的岗位。")
    next_actions = list(dict.fromkeys(action for action in next_actions if action))[:3]

    transferable_strengths = transferable_raw or [
        item.skill for item in skill_evidence if item.bucket in {"partial", "transferable"}
    ][:3]

    core_job_requirements = core_req_raw or _fallback_requirement_summary(job)

    return MatchResultModel(
        score=score,
        level=level,
        dimensions=dims,
        matched_points=matched_points,
        missing_points=missing_points,
        recommendation=application_decision.summary,
        risk_notes=risk_notes,
        core_job_requirements=core_job_requirements,
        hard_condition_result=hard_condition_result,
        skill_evidence=skill_evidence,
        skill_evidence_summary=skill_summary,
        top_strengths=top_strengths,
        main_gaps=main_gaps,
        transferable_strengths=transferable_strengths,
        hr_screening=hr_screening,
        career_alignment=career_alignment,
        application_decision=application_decision,
        next_actions=next_actions,
        confidence=confidence,
        research_summary=research_summary[:6],
    )
