"""岗位表与岗位解析结果表 ORM。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), default="manual")
    company_name: Mapped[str] = mapped_column(String(255), default="")
    job_title: Mapped[str] = mapped_column(String(255), default="")
    city: Mapped[str] = mapped_column(String(64), default="")
    salary: Mapped[str] = mapped_column(String(64), default="")
    education: Mapped[str] = mapped_column(String(64), default="")
    experience: Mapped[str] = mapped_column(String(64), default="")
    # 三层数据：jd_text=原始（OCR/抓取原文，追溯用）；cleaned_jd_text=规则清洗后正文
    jd_text: Mapped[str] = mapped_column(Text, default="")
    cleaned_jd_text: Mapped[str] = mapped_column(Text, default="")
    # 结构化摘要，用于列表预览（80~150 字，无页面噪声）
    jd_summary: Mapped[str] = mapped_column(String(512), default="")
    job_url: Mapped[str] = mapped_column(String(512), default="")
    # 解析状态：pending / parsing / success / failed
    parse_status: Mapped[str] = mapped_column(String(16), default="pending")
    parse_error: Mapped[str] = mapped_column(String(512), default="")
    # 分析模式：summary（默认，只用画像 JSON）/ full（额外喂简历全文，更准但慢）
    analyze_mode: Mapped[str] = mapped_column(String(16), default="summary")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class JobAnalysis(Base):
    __tablename__ = "job_analysis"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_job_analysis_job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, index=True)
    job_type: Mapped[str] = mapped_column(String(128), default="")
    required_skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    preferred_skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    responsibilities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    requirements: Mapped[list | None] = mapped_column(JSON, nullable=True)
    risk_tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 完整 JobProfile
    analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
