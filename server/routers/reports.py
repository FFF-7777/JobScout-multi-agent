"""报告接口：查询、按需生成、Markdown 下载、Excel 导出。"""
from __future__ import annotations

import io

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import get_settings
from database import SessionLocal, get_db
from models import Job, JobAnalysis, MatchResult, Report, Resume
from schemas.job import JobProfile
from schemas.match import MatchResultModel
from schemas.report import ReportOut
from schemas.resume import ResumeProfile
from services import report_agent
from services.concurrency import bounded_map

router = APIRouter(prefix="/api/reports", tags=["reports"])


class ReportGenerateRequest(BaseModel):
    match_result_ids: list[int] = Field(default_factory=list)
    # standard：代码模板，立即生成，不调 LLM；deep：调 LLM 生成深度报告
    mode: str = "standard"


@router.post("/generate-batch")
def generate_reports_batch(req: ReportGenerateRequest, db: Session = Depends(get_db)):
    """按需为选中的匹配结果生成报告。

    - standard：纯代码模板（匹配点 / 缺口 / 风险 / 面试重点），立即返回，无 LLM 成本。
    - deep：调用 Report Agent（LLM）生成含面试题 / BOSS 话术等的深度报告。
    """
    if not req.match_result_ids:
        raise HTTPException(400, "match_result_ids 不能为空")
    mode = (req.mode or "standard").lower()
    if mode not in ("standard", "deep"):
        raise HTTPException(400, "mode 仅支持 standard / deep")

    rows = db.query(MatchResult).filter(MatchResult.id.in_(req.match_result_ids)).all()
    if not rows:
        raise HTTPException(404, "未找到对应的匹配结果")

    # 预加载所需数据（复用已落库的结构化画像，避免重新跑 Resume/Job Agent）
    payloads: list[dict] = []
    for r in rows:
        resume = db.get(Resume, r.resume_id)
        job = db.get(Job, r.job_id)
        if resume is None or job is None or not resume.profile_json or not r.detail_json:
            continue
        resume_profile = ResumeProfile.model_validate(resume.profile_json)
        ja = db.query(JobAnalysis).filter(JobAnalysis.job_id == r.job_id).first()
        if ja and ja.analysis_json:
            job_profile = JobProfile.model_validate(ja.analysis_json)
        else:
            job_profile = JobProfile(
                company_name=job.company_name or "",
                job_title=job.job_title or "",
                city=job.city or "",
                salary=job.salary or "",
            )
        match = MatchResultModel.model_validate(r.detail_json)
        payloads.append(
            {
                "result_id": r.id,
                "resume_profile": resume_profile,
                "job_profile": job_profile,
                "match": match,
                "resume_text": resume.raw_text,
            }
        )
    if not payloads:
        raise HTTPException(400, "无可生成报告的匹配结果（缺少简历画像或匹配详情）")

    def _persist(result_id: int, report_dict: dict, key: str) -> None:
        s = SessionLocal()
        try:
            row = s.get(MatchResult, result_id)
            if row and row.detail_json is not None:
                detail = dict(row.detail_json)
                detail["report"] = report_dict
                row.detail_json = detail
                row.report_cache_key = key
                s.commit()
        finally:
            s.close()

    def _report_key(p, model_role_model: str) -> str:
        return report_agent.build_report_cache_key(
            p["resume_profile"].model_dump(),
            p["job_profile"].model_dump(),
            p["match"].model_dump(),
            model_role_model,
            mode,
        )

    generated = 0
    errors: list[dict] = []
    cache_hits = 0

    if mode == "standard":
        for p in payloads:
            model = get_settings().llm_fast_model or get_settings().llm_model
            key = _report_key(p, model)
            # 报告缓存命中：同 简历+岗位+匹配+模型+模式 时直接复用，跳过重建
            s = SessionLocal()
            try:
                row = s.get(MatchResult, p["result_id"])
                if (
                    row
                    and row.report_cache_key == key
                    and row.detail_json
                    and row.detail_json.get("report")
                ):
                    cache_hits += 1
                    generated += 1
                    continue
            finally:
                s.close()
            try:
                report = report_agent.build_standard_job_report(p["job_profile"], p["match"])
                _persist(p["result_id"], report, key)
                generated += 1
            except Exception as e:  # noqa: BLE001
                errors.append({"result_id": p["result_id"], "error": str(e)})
    else:  # deep
        model = get_settings().llm_reasoning_model or get_settings().llm_model

        def _deep_worker(p):
            key = _report_key(p, model)
            # deep 报告缓存命中同样跳过 LLM
            s = SessionLocal()
            try:
                row = s.get(MatchResult, p["result_id"])
                if (
                    row
                    and row.report_cache_key == key
                    and row.detail_json
                    and row.detail_json.get("report")
                ):
                    return p, None, key, True
            finally:
                s.close()
            rep = report_agent.run(
                p["resume_profile"],
                p["job_profile"],
                p["match"],
                resume_text=p["resume_text"][:8000] if p["resume_text"] else None,
                model_role="fast",
            )
            return p, rep, key, False

        def _deep_on_result(p, payload, err):
            nonlocal cache_hits
            if err is not None or payload is None:
                errors.append({"result_id": p["result_id"], "error": str(err)})
                return
            _, rep, key, hit = payload
            if hit:
                cache_hits += 1
                return
            _persist(p["result_id"], rep.model_dump(), key)

        bounded_map(
            payloads,
            _deep_worker,
            max_concurrency=get_settings().report_agent_concurrency,
            on_result=_deep_on_result,
        )
        generated = len(payloads) - len(errors)

    return {
        "mode": mode,
        "requested": len(req.match_result_ids),
        "generated": generated,
        "cache_hits": cache_hits,
        "errors": errors,
    }


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
