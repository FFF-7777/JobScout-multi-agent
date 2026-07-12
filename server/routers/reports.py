"""报告接口：查询、Markdown 下载、Excel 导出。"""
from __future__ import annotations

import io

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Job, MatchResult, Report
from schemas.report import ReportOut

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("", response_model=list[ReportOut])
def list_reports(db: Session = Depends(get_db)):
    return db.query(Report).order_by(Report.id.desc()).all()


@router.get("/{report_id}", response_model=ReportOut)
def get_report(report_id: int, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    return report


@router.get("/{report_id}/markdown")
def download_markdown(report_id: int, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    filename = f"internscout_report_{report_id}.md"
    return Response(
        content=report.markdown_content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{report_id}/excel")
def download_excel(report_id: int, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    rows = (
        db.query(MatchResult)
        .filter(MatchResult.task_id == report.task_id)
        .order_by(MatchResult.score.desc())
        .all()
    )
    records = []
    for r in rows:
        job = db.get(Job, r.job_id)
        records.append(
            {
                "公司": job.company_name if job else "",
                "岗位": job.job_title if job else "",
                "城市": job.city if job else "",
                "薪资": job.salary if job else "",
                "匹配度": r.score,
                "等级": r.level,
                "建议": r.recommendation,
                "匹配点": " | ".join(r.matched_points or []),
                "缺口": " | ".join(r.missing_points or []),
                "风险": " | ".join(r.risk_notes or []),
            }
        )
    df = pd.DataFrame(records)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="岗位匹配")
    buf.seek(0)
    filename = f"internscout_jobs_{report_id}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
