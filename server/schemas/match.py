"""匹配相关 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DimensionScores(BaseModel):
    """五维度得分（0-100），由 LLM + 规则给出。"""

    tech_stack: float = Field(0.0, ge=0, le=100)  # 技术栈匹配 30%
    project_exp: float = Field(0.0, ge=0, le=100)  # 项目经验匹配 30%
    role_direction: float = Field(0.0, ge=0, le=100)  # 岗位方向匹配 20%
    qualification: float = Field(0.0, ge=0, le=100)  # 学历/年级/求职条件 10%
    logistics: float = Field(0.0, ge=0, le=100)  # 城市/薪资/可投递性 10%


class MatchResultModel(BaseModel):
    """Match Agent 的结构化输出。"""

    score: float = Field(0.0, ge=0, le=100)
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
    cache_hit: bool = False
    # 匹配档位（quick 快速全量预排 / deep 推理 Top-K 深度）与单条状态（success / failed）
    match_mode: str = "deep"
    status: str = "success"
    error_message: str = ""
    # 已生成的报告（基础报告代码模板 / 深度 AI 报告），来自 detail_json.report
    report: dict | None = None
    report_cache_key: str | None = None
    created_at: datetime | None = None
    # 冗余岗位信息，方便前端表格展示
    company_name: str | None = None
    job_title: str | None = None
    city: str | None = None
    salary: str | None = None

    class Config:
        from_attributes = True
