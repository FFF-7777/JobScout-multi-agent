"""匹配相关 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DimensionScores(BaseModel):
    """五维度得分（0-100），由 LLM + 规则给出。"""

    tech_stack: float = 0.0  # 技术栈匹配 30%
    project_exp: float = 0.0  # 项目经验匹配 30%
    role_direction: float = 0.0  # 岗位方向匹配 20%
    qualification: float = 0.0  # 学历/年级/求职条件 10%
    logistics: float = 0.0  # 城市/薪资/可投递性 10%


class MatchResultModel(BaseModel):
    """Match Agent 的结构化输出。"""

    score: float = 0.0
    level: str = "D"  # S/A/B/C/D
    dimensions: DimensionScores = Field(default_factory=DimensionScores)
    matched_points: list[str] = Field(default_factory=list)
    missing_points: list[str] = Field(default_factory=list)
    recommendation: str = ""
    risk_notes: list[str] = Field(default_factory=list)


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
    created_at: datetime | None = None
    # 冗余岗位信息，方便前端表格展示
    company_name: str | None = None
    job_title: str | None = None
    city: str | None = None
    salary: str | None = None

    class Config:
        from_attributes = True
