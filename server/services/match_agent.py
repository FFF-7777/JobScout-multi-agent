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
from schemas.match import DimensionScores, MatchResultModel
from schemas.resume import ResumeProfile
from services import llm_service

# Prompt / 融合逻辑版本号。作为匹配缓存键的一部分：
# 改了匹配提示词或评分融合权重时递增，使旧缓存自动失效、触发重新匹配。
PROMPT_VERSION = "1"

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
) -> str:
    """匹配结果缓存键：相同 简历画像 + 岗位画像 + 模型 + 分析模式 + Prompt版本 时复用。

    返回 32 位十六进制串（sha256 前 32 字符）。
    """
    payload = {
        "resume": resume_profile,
        "job": job_profile,
        "model": model,
        "mode": mode,
        "prompt_version": prompt_version,
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


def build_hard_fail_result(hard_failures: list[str], job: JobProfile) -> MatchResultModel:
    """硬条件预筛不通过时，不调 LLM，直接给出 0 分 / D 级结果。"""
    return MatchResultModel(
        score=0.0,
        level="D",
        dimensions=DimensionScores(),
        matched_points=[],
        missing_points=[f"硬条件不符：{f}" for f in hard_failures],
        recommendation="不符合硬条件，不建议投递",
        risk_notes=list(hard_failures),
    )


def run(
    resume: ResumeProfile,
    job: JobProfile,
    *,
    resume_text: str | None = None,
    model_role: str = "reasoning",
) -> MatchResultModel:
    """匹配评分。

    resume_text：可选，简历原文（截断到 8000 char）。提供时让模型同时看到
    结构化画像 + 原文细节，匹配点/缺口更精准，但 prompt token 翻倍、
    单次 LLM 调用更慢。
    """
    resume_block = json.dumps(resume.model_dump(), ensure_ascii=False)
    if resume_text:
        resume_block += (
            "\n\n--- 简历原文（用于引用项目细节 / 自我评价）---\n"
            + resume_text[:8000]
        )
    llm_out = llm_service.chat_json(
        prompts.MATCH_SYSTEM,
        prompts.MATCH_USER.format(
            resume_profile=resume_block,
            job_profile=json.dumps(job.model_dump(), ensure_ascii=False),
        ),
        model_role=model_role,
    )

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

    return MatchResultModel(
        score=score,
        level=level,
        dimensions=dims,
        matched_points=llm_out.get("matched_points", []) or [],
        missing_points=llm_out.get("missing_points", []) or [],
        recommendation=_recommendation_of(level),
        risk_notes=llm_out.get("risk_notes", []) or [],
    )
