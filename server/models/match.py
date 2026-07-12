"""匹配结果表 ORM。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resume_id: Mapped[int] = mapped_column(Integer, index=True)
    job_id: Mapped[int] = mapped_column(Integer, index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    level: Mapped[str] = mapped_column(String(4), default="D")
    matched_points: Mapped[list | None] = mapped_column(JSON, nullable=True)
    missing_points: Mapped[list | None] = mapped_column(JSON, nullable=True)
    recommendation: Mapped[str] = mapped_column(String(64), default="")
    risk_notes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 完整 MatchResult（含分维度分数）
    detail_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 命中缓存：相同 简历画像+岗位画像+模型+模式+Prompt版本 时跳过 LLM 复用
    cache_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    cache_hit: Mapped[bool] = mapped_column(default=False)
    # 匹配档位：quick（快速模型全量预排）/ deep（推理模型 Top-K 深度）。用于前端标注与重试策略。
    match_mode: Mapped[str] = mapped_column(String(8), default="deep")
    # 单条执行状态：success / failed（失败也落库，便于前端展示错误并支持单条重试）
    status: Mapped[str] = mapped_column(String(16), default="success")
    error_message: Mapped[str] = mapped_column(Text, default="")
    # 报告缓存键：相同 简历+岗位+匹配+模型+报告模式+Prompt版本 时复用已生成报告，跳过 LLM
    report_cache_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
