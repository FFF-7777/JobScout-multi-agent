"""Agent 单条 item 执行记录表（P2#10 可观测性）。

工作流里每个 Agent 节点（主要是 Match Agent）会对 N 个岗位并发跑 LLM。
这张表把「每一条 item 的一次执行」持久化下来，便于前端排查：
- 哪些岗位在跑（status=running）
- 哪些成功了 / 失败了（status=done / failed，附 error）
- 单条耗时（duration_ms）、起止时间、并发槽位（slot）

行由主线程单写者（workflow / 重试接口）写入，避免在 LLM worker 线程里碰 DB。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AgentItemRun(Base):
    __tablename__ = "agent_item_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 所属任务（agent_runs.task_id）
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    # Agent 名（Resume Agent / Job Agent / Match Agent / Report Agent）
    agent_name: Mapped[str] = mapped_column(String(64), default="")
    # 业务 item 标识：Match Agent 这里是 job_id；其他节点可复用同一字段
    item_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    # 档位（仅 Match Agent 两档时区分 quick / deep）；其他节点留空
    tier: Mapped[str] = mapped_column(String(8), default="")
    # 展示用标签，如岗位标题
    item_label: Mapped[str] = mapped_column(String(255), default="")
    # queued / running / done / failed
    status: Mapped[str] = mapped_column(String(16), default="queued")
    error_message: Mapped[str] = mapped_column(Text, default="")
    # 并发槽位（第几个 worker），便于看并发分布
    slot: Mapped[int] = mapped_column(Integer, default=-1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
