"""匹配相关 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DimensionScores(BaseModel):
    """五个核心维度的评分（0-100）。"""

    tech_stack: float = Field(0.0, ge=0, le=100)
    project_exp: float = Field(0.0, ge=0, le=100)
    role_direction: float = Field(0.0, ge=0, le=100)
    qualification: float = Field(0.0, ge=0, le=100)
    logistics: float = Field(0.0, ge=0, le=100)


class HardConditionItem(BaseModel):
    name: str = ""
    status: str = "unknown"  # pass / partial / fail / unknown
    resume_evidence: str = ""
    job_requirement: str = ""


class HardConditionResult(BaseModel):
    status: str = "unknown"  # pass / partial / fail / unknown
    items: list[HardConditionItem] = Field(default_factory=list)


class SkillEvidenceItem(BaseModel):
    """岗位技能要求与简历证据之间的单条映射。"""

    skill: str = ""
    source: str = "required"  # required / preferred
    bucket: str = "not_shown"  # confirmed / partial / transferable / not_shown
    job_requirement: str = ""
    resume_evidence: str = ""
    note: str = ""


class SkillEvidenceSummary(BaseModel):
    required_total: int = 0
    preferred_total: int = 0
    confirmed_count: int = 0
    partial_count: int = 0
    transferable_count: int = 0
    not_shown_count: int = 0


class StrengthItem(BaseModel):
    title: str = ""
    resume_evidence: str = ""
    job_relevance: str = ""


class GapItem(BaseModel):
    title: str = ""
    severity: str = "minor"  # fatal / major / minor
    impact: str = ""
    short_term_fixable: bool = False
    action: str = ""


class HrScreening(BaseModel):
    likely_result: str = "borderline"  # competitive / borderline / unlikely
    main_reason: str = ""


class CareerAlignment(BaseModel):
    score: float = Field(0.0, ge=0, le=100)
    analysis: str = ""


class ApplicationDecision(BaseModel):
    action: str = "apply"  # priority_apply / apply / selective_apply / skip
    summary: str = ""
    reasons: list[str] = Field(default_factory=list)
    exception: str = ""


class ResearchMetadata(BaseModel):
    status: str = "disabled"  # disabled / skipped / success / degraded
    attempted: bool = False
    queries: list[str] = Field(default_factory=list)
    source_notes: list[str] = Field(default_factory=list)
    reason: str = ""
    error: str = ""


class MatchResultModel(BaseModel):
    """Match Agent 的结构化输出。"""

    score: float = Field(0.0, ge=0, le=100)
    level: str = "D"
    dimensions: DimensionScores = Field(default_factory=DimensionScores)

    matched_points: list[str] = Field(default_factory=list)
    missing_points: list[str] = Field(default_factory=list)
    recommendation: str = ""
    risk_notes: list[str] = Field(default_factory=list)

    core_job_requirements: list[str] = Field(default_factory=list)
    hard_condition_result: HardConditionResult = Field(default_factory=HardConditionResult)
    skill_evidence: list[SkillEvidenceItem] = Field(default_factory=list)
    skill_evidence_summary: SkillEvidenceSummary = Field(default_factory=SkillEvidenceSummary)
    top_strengths: list[StrengthItem] = Field(default_factory=list)
    main_gaps: list[GapItem] = Field(default_factory=list)
    transferable_strengths: list[str] = Field(default_factory=list)
    hr_screening: HrScreening = Field(default_factory=HrScreening)
    career_alignment: CareerAlignment = Field(default_factory=CareerAlignment)
    application_decision: ApplicationDecision = Field(default_factory=ApplicationDecision)
    next_actions: list[str] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0, le=100)
    research_summary: list[str] = Field(default_factory=list)
    research_metadata: ResearchMetadata = Field(default_factory=ResearchMetadata)


class MatchRunRequest(BaseModel):
    resume_id: int
    job_ids: list[int] = Field(default_factory=list)


class MatchResultOut(BaseModel):
    id: int
    resume_id: int
    job_id: int
    task_id: str | None = None
    score: float
    level: str
    recommendation: str
    matched_points: list[str] | None = None
    missing_points: list[str] | None = None
    risk_notes: list[str] | None = None
    detail_json: dict | None = None
    cache_hit: bool = False
    match_mode: str = "deep"
    status: str = "success"
    error_message: str = ""
    report: dict | None = None
    report_cache_key: str | None = None
    created_at: datetime | None = None
    company_name: str | None = None
    job_title: str | None = None
    city: str | None = None
    salary: str | None = None

    class Config:
        from_attributes = True


class PaginatedResults(BaseModel):
    items: list[MatchResultOut]
    total: int
    page: int
    page_size: int
