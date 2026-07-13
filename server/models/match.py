"""匹配结果表 ORM。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class MatchResult(Base):
    __tablename__ = "match_results"
    __table_args__ = (
        UniqueConstraint("task_id", "job_id", name="uq_match_results_task_job"),
    )

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
    # P1#11：deep 阶段失败时改用 "partial"——表示 quick 已成功、deep 失败，结果仍可用。
    status: Mapped[str] = mapped_column(String(16), default="success")
    error_message: Mapped[str] = mapped_column(Text, default="")
    # P1#11：两档分数分离 + 深度失败标记。
    # quick_score：quick 阶段分数（始终保留）；deep_score：deep 阶段分数（成功才有）。
    # partial_success：quick 成功但 deep 失败时为 True（结果仍可查看，不被覆盖）。
    # deep_error_message：deep 阶段失败原因（quick 成功仍可展示）。
    quick_score: Mapped[float] = mapped_column(Float, default=0.0)
    deep_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    partial_success: Mapped[bool] = mapped_column(default=False)
    deep_error_message: Mapped[str] = mapped_column(Text, default="")
    # 报告缓存键：相同 简历+岗位+匹配+模型+报告模式+Prompt版本 时复用已生成报告，跳过 LLM
    report_cache_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
