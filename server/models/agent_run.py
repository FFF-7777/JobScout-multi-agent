"""Agent 执行记录表 ORM（用于工作流过程可视化）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_name: Mapped[str] = mapped_column(String(64), default="")
    step_order: Mapped[int] = mapped_column(Integer, default=0)
    # pending / running / success / failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    summary: Mapped[str] = mapped_column(Text, default="")
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    # ETA：中点 eta_seconds（向后兼容）+ 基于 P50/P90 的时间范围，前端展示「约 X~Y 分钟」
    eta_seconds: Mapped[int] = mapped_column(Integer, default=0)
    eta_low: Mapped[int] = mapped_column(Integer, default=0)
    eta_high: Mapped[int] = mapped_column(Integer, default=0)
    current_item: Mapped[str] = mapped_column(String(255), default="")
    # 单岗位执行计数（主要用于 Match Agent 并发可视化）
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    completed_items: Mapped[int] = mapped_column(Integer, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, default=0)
    # 正在并发处理的岗位（[{job_id, job_title}, ...]），前端折叠展示
    in_flight_items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
