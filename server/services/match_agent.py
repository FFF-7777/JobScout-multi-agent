"""Match Agent：对比简历画像与岗位画像，输出匹配评分与等级。

评分融合：
- LLM 给出 5 个维度分（tech_stack / project_exp / role_direction / qualification / logistics）。
- 规则分：技术栈交集覆盖率（岗位 required_skills 中被简历 skills 覆盖的比例）。
- 最终 tech_stack 维度 = 0.6*规则分 + 0.4*LLM 分（规则更客观，防止 LLM 虚高）。
- 总分按权重加权：技术栈 30% / 项目经验 30% / 岗位方向 20% / 学历求职条件 10% / 城市薪资 10%。
"""
from __future__ import annotations

import json

import prompts
from schemas.job import JobProfile
from schemas.match import DimensionScores, MatchResultModel
from schemas.resume import ResumeProfile
from services import llm_service

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


def _recommendation_of(level: str) -> str:
    return {
        "S": "强烈建议投递",
        "A": "优先投递",
        "B": "可以投递",
        "C": "谨慎投递",
        "D": "不建议投递",
    }[level]


def run(
    resume: ResumeProfile,
    job: JobProfile,
    *,
    resume_text: str | None = None,
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
    )

    llm_dims = llm_out.get("dimensions", {}) or {}
    llm_tech = float(llm_dims.get("tech_stack", 0) or 0)
    rule_tech = rule_tech_coverage(resume, job)

    dims = DimensionScores(
        tech_stack=round(0.6 * rule_tech + 0.4 * llm_tech, 1),
        project_exp=float(llm_dims.get("project_exp", 0) or 0),
        role_direction=float(llm_dims.get("role_direction", 0) or 0),
        qualification=float(llm_dims.get("qualification", 0) or 0),
        logistics=float(llm_dims.get("logistics", 0) or 0),
    )

    score = round(
        dims.tech_stack * WEIGHTS["tech_stack"]
        + dims.project_exp * WEIGHTS["project_exp"]
        + dims.role_direction * WEIGHTS["role_direction"]
        + dims.qualification * WEIGHTS["qualification"]
        + dims.logistics * WEIGHTS["logistics"],
        1,
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
