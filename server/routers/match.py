"""匹配结果查询接口。运行匹配统一走工作流（/api/agents/run）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Job, MatchResult
from schemas.match import MatchResultOut

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
    if row.detail_json:
        out.report = row.detail_json.get("report")
    return out


@router.get("/results", response_model=list[MatchResultOut])
def list_results(
    task_id: str | None = None,
    resume_id: int | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(MatchResult)
    if task_id:
        q = q.filter(MatchResult.task_id == task_id)
    if resume_id:
        q = q.filter(MatchResult.resume_id == resume_id)
    rows = q.order_by(MatchResult.score.desc()).all()
    return [_enrich(r, db) for r in rows]


@router.get("/results/{result_id}", response_model=MatchResultOut)
def get_result(result_id: int, db: Session = Depends(get_db)):
    row = db.get(MatchResult, result_id)
    if row is None:
        raise HTTPException(404, "匹配结果不存在")
    return _enrich(row, db)
