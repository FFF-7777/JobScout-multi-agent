"""Match Agent：对比简历画像与岗位画像，输出匹配评分与等级。

评分融合：
- LLM 给出 5 个维度分（tech_stack / project_exp / role_direction / qualification / logistics）。
- 规则分：技术栈交集覆盖率（岗位 required_skills 中被简历 skills 覆盖的比例）。
- 最终 tech_stack 维度 = 0.6*规则分 + 0.4*LLM 分（规则更客观，防止 LLM 虚高）。
- 总分按权重加权：技术栈 30% / 项目经验 30% / 岗位方向 20% / 学历求职条件 10% / 城市薪资 10%。
"""
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
from services import llm_service

# Prompt / 融合逻辑版本号。作为匹配缓存键的一部分：
# 改了匹配提示词或评分融合权重时递增，使旧缓存自动失效、触发重新匹配。
PROMPT_VERSION = "2"  # v2: 结构化核心优势/短板/HR 初筛/投递决策
MATCH_POLICY_VERSION = "3"  # v3: 投递决策合并硬条件+分数+方向

WEIGHTS = {
    "tech_stack": 0.30,
    "project_exp": 0.30,
    "role_direction": 0.20,
    "qualification": 0.10,
    "logistics": 0.10,
}


def _normalize(s: str) -> str:
    return s.strip().lower().replace(" ", "").replace(".", "").replace("-", "")


def rule_tech_coverage(resume: ResumeProfile, job: JobProfile) -> float:
    """技术栈交集覆盖率（0-100）。"""
    required = job.required_skills or []
    if not required:
        return 60.0  # JD 未列明必备技能，给中性分
    resume_skills = {_normalize(s) for s in resume.skills}
    # 也把项目关键词纳入技能池
    for p in resume.projects:
        for kw in p.keywords:
            resume_skills.add(_normalize(kw))

    hit = 0
    for req in required:
        rq = _normalize(req)
        if any(rq in rs or rs in rq for rs in resume_skills if rs):
            hit += 1
    return round(hit / len(required) * 100, 1)


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
    policy_version: str = MATCH_POLICY_VERSION,
) -> str:
    """匹配结果缓存键。

    相同 简历画像 + 岗位画像 + 模型 + 分析模式 + 档位(tier) + 简历原文哈希 +
    评分策略版本 + 权重 时复用，返回 32 位十六进制串（sha256 前 32 字符）。

    P0#3 关键修复：旧键不含 tier / 简历原文哈希 / 策略版本，默认配置下 quick 与 deep
    同模型同键，deep 阶段会直接命中 quick 结果、跳过深度复核。现把 tier 与
    resume_text_hash（仅 deep+full 有值）纳入键，确保 quick 与 deep 不会互相命中；
    并加入 policy_version / weights，评分口径变化时旧缓存自动失效。
    """
    payload = {
        "resume": resume_profile,
        "job": job_profile,
        "model": model,
        "mode": mode,
        "tier": tier,
        "resume_text_hash": resume_text_hash,
        "prompt_version": prompt_version,
        "policy_version": policy_version,
        "weights": WEIGHTS,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _clamp_score(v: object) -> float:
    """把任意模型输出安全地截断到 [0, 100]，避免异常分导致 Pydantic 校验失败。"""
    try:
        x = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if x != x:  # NaN 检查
        return 0.0
    return max(0.0, min(100.0, x))


def _recommendation_of(level: str) -> str:
    return {
        "S": "强烈建议投递",
        "A": "优先投递",
        "B": "可以投递",
        "C": "谨慎投递",
        "D": "不建议投递",
    }[level]


def build_hard_fail_result(
    hard_failures: list[str],
    job: JobProfile,
    hard_items: list[HardConditionItem] | None = None,
) -> MatchResultModel:
    """硬条件预筛不通过时，不调 LLM，直接给出 skip 决策。"""
    items = hard_items or [HardConditionItem(name=f, status="fail") for f in hard_failures]
    return MatchResultModel(
        score=0.0,
        level="D",
        dimensions=DimensionScores(),
        matched_points=[],
        missing_points=[f"硬条件不符：{f}" for f in hard_failures],
        recommendation="不符合硬条件，不建议投递",
        risk_notes=list(hard_failures),
        hard_condition_result=HardConditionResult(status="fail", items=items),
        application_decision=ApplicationDecision(
            action="skip",
            summary="硬条件不满足，跳过投递",
        ),
        confidence=100.0,
    )


# 深度复核阶段在用户 prompt 末尾追加的强调（方案 B：quick/deep 用不同输入信息，
# 即便同模型也确实做了一次更严格的复核，而不是原样重复调用）。
_DEEP_REVIEW_SUFFIX = (
    "\n\n[深度复核模式] 这是深度复核阶段，请在以上基础上更严格地核对每一项匹配点："
    "移除任何证据不足的夸大项，对存疑的 1-2 点明确标注「不确定」，"
    "并结合简历项目细节说明匹配/不匹配的具体理由。"
)


def _compute_application_decision(
    score: float,
    hard_fail: bool,
    career_score: float,
    role_direction_score: float,
) -> str:
    """基于规则计算投递决策。

    优先考虑硬条件（hard_fail）与职业方向（career_score）；
    分数低于阈值时即使 career 正常也降级。
    """
    if hard_fail:
        return "skip"
    if career_score < 40 or role_direction_score < 30:
        return "selective_apply" if score >= 55 else "skip"
    if score >= 80:
        return "priority_apply"
    if score >= 65:
        return "apply"
    if score >= 50:
        return "selective_apply"
    return "skip"


def _decision_summary_of(action: str, score: float) -> str:
    return {
        "priority_apply": f"匹配度 {score} 分，核心方向一致，建议优先投递",
        "apply": f"匹配度 {score} 分，核心能力匹配，建议投递",
        "selective_apply": f"匹配度 {score} 分，部分方向有差距，建议选择性投递",
        "skip": f"匹配度 {score} 分，硬条件或方向不匹配，不建议投递",
    }.get(action, "")


def run(
    resume: ResumeProfile,
    job: JobProfile,
    *,
    resume_text: str | None = None,
    model_role: str = "reasoning",
    tier: str = "deep",
    hard_conditions: list[dict] | None = None,
) -> MatchResultModel:
    """匹配评分与投递决策。

    resume_text：可选，简历原文（截断到 8000 char）。提供时让模型同时看到
    结构化画像 + 原文细节，匹配点/缺口更精准，但 prompt token 翻倍。
    tier：quick（全量快速预排）/ deep（Top-K 或用户指定 full 的深度复核）。
    hard_conditions：上游 precheck_job 输出的硬条件结构化结果（可选）。
    """
    resume_block = json.dumps(resume.model_dump(), ensure_ascii=False)
    if resume_text:
        resume_block += (
            "\n\n--- 简历原文（用于引用项目细节 / 自我评价）---\n"
            + resume_text[:8000]
        )
    user = prompts.MATCH_USER.format(
        resume_profile=resume_block,
        job_profile=json.dumps(job.model_dump(), ensure_ascii=False),
    )
    if tier == "deep":
        user += _DEEP_REVIEW_SUFFIX
    llm_out = llm_service.chat_json(
        prompts.MATCH_SYSTEM,
        user,
        model_role=model_role,
    )

    # ── 五维评分（LLM + 规则融合）──
    llm_dims = llm_out.get("dimensions", {}) or {}
    llm_tech = _clamp_score(llm_dims.get("tech_stack", 0))
    rule_tech = rule_tech_coverage(resume, job)

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

    # ── 结构化字段（从 LLM 输出解析，有兜底默认值）──
    top_strengths_raw: list[dict] = llm_out.get("top_strengths", []) or []
    main_gaps_raw: list[dict] = llm_out.get("main_gaps", []) or []
    hr_raw: dict = llm_out.get("hr_screening", {}) or {}
    career_raw: dict = llm_out.get("career_alignment", {}) or {}

    # 从 LLM 输出构建结构化字段（有限量兜底）
    top_strengths: list[StrengthItem] = [
        StrengthItem(
            title=s.get("title", ""),
            resume_evidence=s.get("resume_evidence", ""),
            job_relevance=s.get("job_relevance", ""),
        )
        for s in (top_strengths_raw or [])
    ][:3]  # 最多 3 条

    main_gaps: list[GapItem] = [
        GapItem(
            title=g.get("title", ""),
            severity=g.get("severity", "major"),
            impact=g.get("impact", ""),
            short_term_fixable=bool(g.get("short_term_fixable", False)),
            action=g.get("action", ""),
        )
        for g in (main_gaps_raw or [])
    ][:3]  # 最多 3 条

    career_alignment = CareerAlignment(
        score=_clamp_score(career_raw.get("score", 50)),
        analysis=career_raw.get("analysis", ""),
    )
    hr_screening = HrScreening(
        likely_result=hr_raw.get("likely_result", "borderline"),
        main_reason=hr_raw.get("main_reason", ""),
    )
    confidence = _clamp_score(llm_out.get("confidence", 70))

    # ── 硬条件判断（从上游 hard_conditions 参数或 precheck 结果构建）──
    hard_fail = False
    hard_items: list[HardConditionItem] = []
    if hard_conditions:
        for hc in hard_conditions:
            name = hc.get("name", "")
            status = hc.get("status", "unknown")
            if status == "fail":
                hard_fail = True
            hard_items.append(
                HardConditionItem(
                    name=name,
                    status=status,
                    resume_evidence=hc.get("resume_evidence", ""),
                    job_requirement=hc.get("job_requirement", ""),
                )
            )
    hard_condition_result = HardConditionResult(
        status="fail" if hard_fail else "pass",
        items=hard_items,
    )

    # ── 投递决策（服务器端规则，不依赖 LLM）──
    action = _compute_application_decision(
        score=score,
        hard_fail=hard_fail,
        career_score=career_alignment.score,
        role_direction_score=dims.role_direction,
    )
    application_decision = ApplicationDecision(
        action=action,
        summary=_decision_summary_of(action, score),
    )

    # ── 向后兼容字段（从结构化字段推导）──
    matched_points = [s.title for s in top_strengths]
    missing_points = [
        f"[{g.severity.upper()}] {g.title}{' — ' + g.impact if g.impact else ''}"
        for g in main_gaps
    ]
    risk_notes_raw: list[str] = llm_out.get("risk_notes", []) or []
    risk_notes = [
        g.action for g in main_gaps if g.action
    ] + risk_notes_raw
    if hr_screening.likely_result == "unlikely":
        risk_notes.insert(0, f"HR 初筛风险：{hr_screening.main_reason}")
    risk_notes = risk_notes[:3]  # 最多 3 条

    recommendation = _decision_summary_of(action, score)

    # ── 行动建议 ──
    next_actions_raw: list[str] = llm_out.get("next_actions", []) or []
    next_actions = (
        [g.action for g in main_gaps if g.action and len(g.action) > 3]
        + next_actions_raw
    )[:3]

    transferable_raw: list[str] = llm_out.get("transferable_strengths", []) or []
    core_req_raw: list[str] = llm_out.get("core_job_requirements", []) or []

    return MatchResultModel(
        score=score,
        level=level,
        dimensions=dims,
        # 旧字段（向后兼容）
        matched_points=matched_points,
        missing_points=missing_points,
        recommendation=recommendation,
        risk_notes=risk_notes,
        # 新结构化字段
        core_job_requirements=core_req_raw,
        hard_condition_result=hard_condition_result,
        top_strengths=top_strengths,
        main_gaps=main_gaps,
        transferable_strengths=transferable_raw,
        hr_screening=hr_screening,
        career_alignment=career_alignment,
        application_decision=application_decision,
        next_actions=next_actions,
        confidence=confidence,
    )
