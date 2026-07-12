"""岗位相关 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class JobProfile(BaseModel):
    """Job Agent 的结构化输出：岗位画像。"""

    company_name: str = ""
    job_title: str = ""
    city: str = ""
    salary: str = ""
    education: str = ""
    experience: str = ""
    job_type: str = ""
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    # 风险标签：外包 / 培训 / 销售 / 运营 / 助教 / 不相关
    risk_tags: list[str] = Field(default_factory=list)


class JobImportTextRequest(BaseModel):
    jd_text: str = Field(..., description="单个或多个 JD 文本；批量用分隔线")
    split_batch: bool = Field(
        default=False, description="是否将文本按空行/分隔线拆成多个岗位"
    )


class JobImportUrlRequest(BaseModel):
    url: str = Field(..., description="岗位详情页链接（如 BOSS 直聘、拉勾等）")


class JobOut(BaseModel):
    id: int
    source: str
    company_name: str
    job_title: str
    city: str
    salary: str
    education: str
    experience: str
    jd_text: str
    job_url: str
    analyze_mode: str = "summary"
    created_at: datetime | None = None
    analysis: dict | None = None

    class Config:
        from_attributes = True
