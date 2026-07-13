"""单岗位报告独立存储表（P1#12）。

把报告从 MatchResult.detail_json["report"] 抽离到独立表，
使 standard/deep 报告各自成行、不相覆盖，并支持按模式检索。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class JobReport(Base):
    __tablename__ = "job_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_result_id: Mapped[int] = mapped_column(Integer, index=True)
    mode: Mapped[str] = mapped_column(String(16), default="standard")  # standard / deep
    report_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cache_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
