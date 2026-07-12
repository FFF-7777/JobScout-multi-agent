"""Agent 工作流接口：启动分析任务、查询执行进度。"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import AgentRun, Job, Resume
from schemas.report import AgentRunOut, WorkflowRunRequest, WorkflowTaskOut
from services import workflow

router = APIRouter(prefix="/api/agents", tags=["agents"])


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

    task_id = workflow.create_task(req.resume_id, job_ids)
    background.add_task(workflow.run_workflow, task_id, req.resume_id, job_ids)

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
