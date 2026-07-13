"""匹配结果查询 / 重试 / 单条执行记录接口。运行匹配统一走工作流（/api/agents/run）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import Job, MatchResult, Resume
from schemas.match import MatchResultOut, PaginatedResults
from services import match_core
from services.item_run import list_item_runs

router = APIRouter(prefix="/api/match", tags=["match"])


def _enrich(row: MatchResult, db: Session) -> MatchResultOut:
    out = MatchResultOut.model_validate(row)
    job = db.get(Job, row.job_id)
    if job:
        out.company_name = job.company_name
        out.job_title = job.job_title
        out.city = job.city
        out.salary = job.salary
    out.cache_hit = bool(row.cache_hit)
    out.match_mode = row.match_mode or "deep"
    out.status = row.status or "success"
    out.error_message = row.error_message or ""
    out.report_cache_key = row.report_cache_key
    if row.detail_json:
        out.report = row.detail_json.get("report")
    return out


@router.get("/results", response_model=PaginatedResults)
def list_results(
    task_id: str | None = None,
    resume_id: int | None = None,
    page: int = 1,
    page_size: int = 30,
    db: Session = Depends(get_db),
):
    q = db.query(MatchResult)
    if task_id:
        q = q.filter(MatchResult.task_id == task_id)
    if resume_id:
        q = q.filter(MatchResult.resume_id == resume_id)
    total = q.count()
    rows = (
        q.order_by(MatchResult.score.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [_enrich(r, db) for r in rows]
    return PaginatedResults(items=items, total=total, page=page, page_size=page_size)


@router.get("/results/{result_id}", response_model=MatchResultOut)
def get_result(result_id: int, db: Session = Depends(get_db)):
    row = db.get(MatchResult, result_id)
    if row is None:
        raise HTTPException(404, "匹配结果不存在")
    return _enrich(row, db)


@router.get("/results/by-job/{job_id}", response_model=MatchResultOut | None)
def get_result_by_job(job_id: int, db: Session = Depends(get_db)):
    """按 job_id 查最新一条匹配结果（无论属于哪个 task）。

    用于 JobDetailView modal 嵌入场景——不依赖 store.taskId，
    刷新页面 / 切换 task 也能显示对应岗位的最近分析。
    """
    row = (
        db.query(MatchResult)
        .filter(MatchResult.job_id == job_id)
        .order_by(MatchResult.id.desc())
        .first()
    )
    if row is None:
        return None
    return _enrich(row, db)


class _RetryRequest(BaseModel):
    # 要重试的匹配结果 id；不传则重试该 task 下所有 failed 的结果
    result_ids: list[int] = Field(default_factory=list)


@router.delete("/results/{result_id}")
def delete_result(result_id: int, db: Session = Depends(get_db)):
    """删除单条匹配结果（同时清理关联的 job_reports 与 item_runs）。"""
    row = db.get(MatchResult, result_id)
    if row is None:
        raise HTTPException(404, "匹配结果不存在")
    # 清理关联报告 + 单条执行记录
    from models import AgentItemRun, JobReport
    db.query(JobReport).filter(JobReport.match_result_id == result_id).delete()
    db.query(AgentItemRun).filter(
        AgentItemRun.task_id == row.task_id,
        AgentItemRun.item_id == row.job_id,
    ).delete()
    db.delete(row)
    db.commit()
    return {"ok": True, "id": result_id}


@router.post("/results/batch-delete")
def batch_delete_results(
    req: _RetryRequest,
    db: Session = Depends(get_db),
):
    """批量删除匹配结果（req.result_ids）。"""
    if not req.result_ids:
        raise HTTPException(400, "result_ids 不能为空")
    from models import AgentItemRun, JobReport
    rows = db.query(MatchResult).filter(MatchResult.id.in_(req.result_ids)).all()
    if not rows:
        raise HTTPException(404, "没有可删除的匹配结果")
    job_ids = {r.job_id for r in rows}
    task_ids = {r.task_id for r in rows if r.task_id}
    db.query(JobReport).filter(JobReport.match_result_id.in_(req.result_ids)).delete(synchronize_session=False)
    if task_ids:
        db.query(AgentItemRun).filter(
            AgentItemRun.task_id.in_(task_ids),
            AgentItemRun.item_id.in_(job_ids),
        ).delete(synchronize_session=False)
    for r in rows:
        db.delete(r)
    db.commit()
    return {"ok": True, "deleted": len(rows)}


@router.post("/results/retry")
def retry_results(
    req: _RetryRequest,
    task_id: str | None = None,
    resume_id: int | None = None,
    db: Session = Depends(get_db),
):
    """单条 / 批量重试失败的匹配结果（P2#14）。

    - 不传 result_ids：重试指定 task_id（或 resume_id）下所有 status=failed 的结果
    - 传 result_ids：只重试这些（无论是否 failed 都强制重跑，便于手动刷新）
    重试统一用 deep 档（推理模型）重算，刷新对应 match_results 行。
    """
    q = db.query(MatchResult)
    if req.result_ids:
        q = q.filter(MatchResult.id.in_(req.result_ids))
    else:
        q = q.filter(MatchResult.status == "failed")
        if task_id:
            q = q.filter(MatchResult.task_id == task_id)
        if resume_id:
            q = q.filter(MatchResult.resume_id == resume_id)
    rows = q.all()
    if not rows:
        raise HTTPException(404, "没有可重试的匹配结果")
    # 去重：同一 (task_id, job_id) 只取最新一行
    targets = {}
    for r in rows:
        if r.job_id is None:
            continue
        targets[(r.task_id, r.job_id)] = r
    generated = 0
    errors: list[dict] = []
    for (tid, jid), r in targets.items():
        rid = r.id
        rtask_id = tid
        rresume_id = r.resume_id
        # 取简历画像（重试走 deep 档，需要 resume_id 拿原文）
        res = db.get(Resume, rresume_id)
        if res is None or not res.profile_json:
            errors.append({"result_id": rid, "error": "缺少简历画像，无法重试"})
            continue
        oc = match_core.run_single_match(res.profile_json, rresume_id, jid, tier="deep")
        if oc.match is None:
            match_core.persist_match_row(
                rtask_id, rresume_id, jid, None, oc.key, oc.cache_hit,
                match_mode="deep", status="failed", error=oc.error or "重试仍失败",
            )
            errors.append({"result_id": rid, "error": oc.error or "重试仍失败"})
            continue
        match_core.persist_match_row(
            rtask_id, rresume_id, jid, oc.match, oc.key, oc.cache_hit,
            match_mode="deep", status="success",
        )
        generated += 1
    db.commit()
    return {
        "requested": len(targets),
        "generated": generated,
        "errors": errors,
    }


@router.get("/item-runs", response_model=list[dict])
def get_item_runs(task_id: str, agent_name: str | None = None):
    """查询某任务的单条执行记录（P2#10），供前端「执行明细」面板展示。"""
    rows = list_item_runs(task_id, agent_name)
    return [
        {
            "id": r.id,
            "task_id": r.task_id,
            "agent_name": r.agent_name,
            "item_id": r.item_id,
            "tier": r.tier,
            "item_label": r.item_label,
            "status": r.status,
            "error_message": r.error_message,
            "slot": r.slot,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_ms": r.duration_ms,
        }
        for r in rows
    ]
