"""简历相关 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectItem(BaseModel):
    name: str = ""
    description: str = ""
    keywords: list[str] = Field(default_factory=list)


class ResumeProfile(BaseModel):
    """Resume Agent 的结构化输出：候选人画像。"""

    name: str = ""
    target_roles: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    # 硬条件预筛用（供 Match 前的规则判断，不消耗 LLM）
    graduation_year: int | None = None  # 毕业年份，如 2027
    available_days_per_week: int | None = None  # 每周可实习天数


class ResumeParseRequest(BaseModel):
    text: str = Field(..., description="简历纯文本")
    filename: str = "pasted.txt"


class ResumeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    raw_text: str
    profile_json: ResumeProfile | None = None
    ocr_metadata: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

class ProfileUpdateRequest(BaseModel):
    profile_json: ResumeProfile


class ResumeImportImageFailed(BaseModel):
    filename: str
    error: str


class ResumeImportImagesResult(BaseModel):
    resume: ResumeOut
    total: int
    success: int
    provider: str
    failed: list[ResumeImportImageFailed] = Field(default_factory=list)
