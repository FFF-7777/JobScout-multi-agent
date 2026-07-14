"""Agent 工作流接口：启动分析任务、查询执行进度、中断任务。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from config import get_settings
from models import AgentRun, Job, Resume
from schemas.report import AgentRunOut, WorkflowRunRequest, WorkflowTaskOut
from services import workflow

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/runtime-meta")
def get_runtime_meta():
    settings = get_settings()
    return {
        "job_agent_concurrency": settings.job_agent_concurrency,
        "match_agent_concurrency": settings.match_agent_concurrency,
        "report_agent_concurrency": settings.report_agent_concurrency,
        "match_two_tier": settings.match_two_tier,
        "network_capabilities": {
            "quick_analysis": "disabled",
            "deep_analysis": "forced_with_model_fallback",
            "deep_report": "forced_with_model_fallback",
        },
        "assumptions": {
            "quick_seconds_per_job": 40,
            "deep_seconds_per_job": 120,
            "report_overhead_seconds": 5,
        },
    }


@router.post("/run", response_model=WorkflowTaskOut)
def run_agents(
    req: WorkflowRunRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    resume = db.get(Resume, req.resume_id)
    if resume is None:
        raise HTTPException(404, "简历不存在")
    job_ids = req.job_ids or [j.id for j in db.query(Job).all()]
    if not job_ids:
        raise HTTPException(400, "没有可分析的岗位")

    # 启动限制：本次任务选中的「深度分析」岗位数上限由 config 控制。
    # 默认 0 = 不限制；>0 时按上限拒绝，避免一次选过多导致 LLM 成本/耗时失控。
    # 仅约束本次任务集合，不限制数据库里 full 岗位总数（避免历史 full 影响后续任务）。
    limit = get_settings().full_mode_limit
    if limit > 0:
        full_count = (
            db.query(Job)
            .filter(Job.id.in_(job_ids), Job.analyze_mode == "full")
            .count()
        )
        if full_count > limit:
            raise HTTPException(
                400,
                f"深度分析（full）岗位本次最多 {limit} 个，当前选中 {full_count} 个，请减少后再试",
            )

    task_id = workflow.create_task(req.resume_id, job_ids)
    background.add_task(
        workflow.run_workflow,
        task_id,
        req.resume_id,
        job_ids,
    )

    steps = (
        db.query(AgentRun)
        .filter(AgentRun.task_id == task_id)
        .order_by(AgentRun.step_order)
        .all()
    )
    return WorkflowTaskOut(
        task_id=task_id,
        status="running",
        steps=[AgentRunOut.model_validate(s) for s in steps],
    )


def _task_status(steps: list[AgentRun]) -> str:
    statuses = {s.status for s in steps}
    if "failed" in statuses and not ({"pending", "running"} & statuses):
        # 有失败且没有进行中的步骤
        return "failed" if all(s.status in ("failed",) for s in steps) else "completed_with_errors"
    if statuses <= {"success"}:
        return "completed"
    if "running" in statuses or "pending" in statuses:
        return "running"
    return "completed"


@router.post("/tasks/{task_id}/abort")
def abort_task(task_id: str, db: Session = Depends(get_db)):
    """把一个运行中任务的未完成步骤标记为 failed（用户中止）。
    已 success 的步骤保留；后台 workflow 会在下一次 on_result 检查时发现并早退。"""
    runs = (
        db.query(AgentRun)
        .filter(AgentRun.task_id == task_id, AgentRun.status.in_(["pending", "running"]))
        .all()
    )
    if not runs:
        raise HTTPException(404, "没有可中断的运行中任务")
    now = datetime.utcnow()
    for r in runs:
        r.status = "failed"
        r.error_message = "用户中止"
        if r.started_at is None:
            r.started_at = now
        r.finished_at = now
        r.progress = 100
    db.commit()
    return {"ok": True, "task_id": task_id, "aborted": [r.agent_name for r in runs]}


@router.get("/tasks/{task_id}", response_model=WorkflowTaskOut)
def get_task(task_id: str, db: Session = Depends(get_db)):
    steps = (
        db.query(AgentRun)
        .filter(AgentRun.task_id == task_id)
        .order_by(AgentRun.step_order)
        .all()
    )
    if not steps:
        raise HTTPException(404, "任务不存在")
    return WorkflowTaskOut(
        task_id=task_id,
        status=_task_status(steps),
        steps=[AgentRunOut.model_validate(s) for s in steps],
    )


@router.get("/tasks/{task_id}/steps", response_model=list[AgentRunOut])
def get_steps(task_id: str, db: Session = Depends(get_db)):
    steps = (
        db.query(AgentRun)
        .filter(AgentRun.task_id == task_id)
        .order_by(AgentRun.step_order)
        .all()
    )
    if not steps:
        raise HTTPException(404, "任务不存在")
    return [AgentRunOut.model_validate(s) for s in steps]
