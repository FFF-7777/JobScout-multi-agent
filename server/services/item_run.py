"""AgentItemRun 单写者辅助（P2#10 可观测性）。

所有写入都在主线程（workflow 节点 / 重试接口）完成，避免在 LLM worker 线程碰 DB。
按 (task_id, agent_name, item_id, tier) 做 upsert，两档匹配会各自留一行。
"""
from __future__ import annotations

from datetime import datetime

from database import SessionLocal
from models import AgentItemRun


def upsert_item_run(
    task_id: str,
    agent_name: str,
    item_id: int,
    item_label: str,
    *,
    tier: str = "",
    status: str | None = None,
    error: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    duration_ms: int | None = None,
    slot: int | None = None,
    phase: str | None = None,
    metadata: dict | None = None,
) -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(AgentItemRun)
            .filter(
                AgentItemRun.task_id == task_id,
                AgentItemRun.agent_name == agent_name,
                AgentItemRun.item_id == item_id,
                AgentItemRun.tier == tier,
            )
            .first()
        )
        if row is None:
            row = AgentItemRun(
                task_id=task_id,
                agent_name=agent_name,
                item_id=item_id,
                tier=tier,
                item_label=item_label,
                status="queued",
            )
            db.add(row)
        if status is not None:
            row.status = status
        if item_label:
            row.item_label = item_label
        if error is not None:
            row.error_message = error
        if started_at is not None:
            row.started_at = started_at
        if finished_at is not None:
            row.finished_at = finished_at
        if duration_ms is not None:
            row.duration_ms = duration_ms
        if slot is not None:
            row.slot = slot
        if phase is not None:
            row.phase = phase
        if metadata is not None:
            row.metadata_json = metadata
        db.commit()
    finally:
        db.close()


def list_item_runs(task_id: str, agent_name: str | None = None) -> list[AgentItemRun]:
    db = SessionLocal()
    try:
        q = db.query(AgentItemRun).filter(AgentItemRun.task_id == task_id)
        if agent_name:
            q = q.filter(AgentItemRun.agent_name == agent_name)
        return q.order_by(AgentItemRun.id.asc()).all()
    finally:
        db.close()
