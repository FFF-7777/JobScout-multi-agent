"""深度报告后台任务表（P0#6）：把 POST /api/reports/generate-batch 改为异步。

接口立即返回 task_id，后台线程生成报告，前端轮询状态，避免前端 Axios 120s 超时
但后端仍在跑长任务。基础报告（代码模板）仍同步即时；deep 报告走此后台任务。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ReportTask(Base):
    __tablename__ = "report_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 对外暴露的任务 id（uuid hex），前端轮询用
    task_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(16), default="standard")  # standard / deep
    # queued / running / done / failed / partial（部分失败）
    status: Mapped[str] = mapped_column(String(16), default="queued")
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
