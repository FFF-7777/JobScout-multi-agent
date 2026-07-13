"""后台岗位解析任务队列（P1#9）。

代替 threading.Thread + ThreadPoolExecutor 的临时方案——任务写入此表，
后台 daemon 线程轮询，把 queued 标记为 running 后调 Job Agent 解析。
服务重启时自动捡起卡在 queued/running 的任务重新调度，不再"永远 parsing"。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class JobParseTask(Base):
    __tablename__ = "job_parse_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, index=True)
    # queued / running / done / failed
    status: Mapped[str] = mapped_column(String(16), default="queued")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
