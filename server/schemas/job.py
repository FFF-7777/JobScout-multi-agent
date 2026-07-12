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
    # 实习相关（非实习岗位留空 / None）
    internship_days_per_week: int | None = None
    internship_duration: str = ""
    graduation_years: list[int] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    # 风险标签：外包 / 培训 / 销售 / 运营 / 助教 / 不相关
    risk_tags: list[str] = Field(default_factory=list)
    # 80~150 字岗位摘要，用于列表预览（不含收藏/举报等页面噪声）
    jd_summary: str = ""


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
    jd_summary: str = ""
    job_url: str
    parse_status: str = "pending"
    parse_error: str = ""
    analyze_mode: str = "summary"
    created_at: datetime | None = None
    analysis: dict | None = None

    class Config:
        from_attributes = True


class ImportImageFailed(BaseModel):
    """单张图片识别失败的原因。"""
    filename: str
    error: str


class ImportImagesResult(BaseModel):
    """图片批量导入结果：成功创建的岗位 + 失败明细。"""
    created: list[JobOut] = Field(default_factory=list)
    failed: list[ImportImageFailed] = Field(default_factory=list)
