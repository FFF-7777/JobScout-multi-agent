"""岗位相关接口。"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models import Job, JobAnalysis, MatchResult
from schemas.job import JobImportTextRequest, JobImportUrlRequest, JobOut
from services import document_parser, job_agent, url_fetcher

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_SPLIT_RE = re.compile(r"\n\s*(?:-{3,}|={3,}|#{2,}|\n)\s*\n")
_MAX_UPLOAD = 10 * 1024 * 1024  # 10 MiB 上传上限


def _to_out(job: Job, db: Session) -> dict:
    analysis = db.query(JobAnalysis).filter(JobAnalysis.job_id == job.id).first()
    out = JobOut.model_validate(job).model_dump()
    out["analysis"] = analysis.analysis_json if analysis else None
    return out


@router.post("/import-text", response_model=list[JobOut])
def import_text(req: JobImportTextRequest, db: Session = Depends(get_db)):
    text = req.jd_text.strip()
    if not text:
        raise HTTPException(400, "JD 文本为空")
    chunks = [c.strip() for c in _SPLIT_RE.split(text) if c.strip()] if req.split_batch else [text]
    created: list[dict] = []
    for chunk in chunks:
        job = Job(source="manual", jd_text=chunk)
        db.add(job)
        db.commit()
        db.refresh(job)
        created.append(_to_out(job, db))
    return created


@router.post("/import-file", response_model=list[JobOut])
async def import_file(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    data = await file.read()
    if len(data) > _MAX_UPLOAD:
        raise HTTPException(413, f"文件过大（上限 {_MAX_UPLOAD // 1024 // 1024} MiB）")
    try:
        rows = document_parser.parse_jobs_table(file.filename or "jobs", data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not rows:
        raise HTTPException(400, "表格中未解析到有效岗位")
    created: list[dict] = []
    for row in rows:
        job = Job(**row)
        db.add(job)
        db.commit()
        db.refresh(job)
        created.append(_to_out(job, db))
    return created


@router.post("/import-url", response_model=JobOut)
def import_url(req: JobImportUrlRequest, db: Session = Depends(get_db)):
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "链接为空")
    try:
        jd_text = url_fetcher.fetch(url)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not jd_text.strip():
        raise HTTPException(400, "未能从链接中提取到文本内容")
    job = Job(source="url", job_url=url, jd_text=jd_text[:30000])
    db.add(job)
    db.commit()
    db.refresh(job)
    return _to_out(job, db)


@router.get("", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db)):
    jobs = db.query(Job).order_by(Job.id.desc()).all()
    return [_to_out(j, db) for j in jobs]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "岗位不存在")
    return _to_out(job, db)


@router.post("/{job_id}/analyze", response_model=JobOut)
def analyze_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "岗位不存在")
    hints = {
        "company_name": job.company_name,
        "job_title": job.job_title,
        "city": job.city,
        "salary": job.salary,
    }
    try:
        profile = job_agent.run(job.jd_text, hints)
    except Exception:
        raise HTTPException(502, "岗位分析失败：模型返回异常，请稍后重试")
    _apply_analysis(job, profile, db)
    db.refresh(job)
    return _to_out(job, db)


def _apply_analysis(job: Job, profile, db: Session) -> None:
    """把 JobProfile 写回 job 主表与 job_analysis 表。供 analyze 与 batch 复用。"""
    job.company_name = job.company_name or profile.company_name
    job.job_title = job.job_title or profile.job_title
    job.city = job.city or profile.city
    job.salary = job.salary or profile.salary
    existing = db.query(JobAnalysis).filter(JobAnalysis.job_id == job.id).first()
    if existing is None:
        existing = JobAnalysis(job_id=job.id)
        db.add(existing)
    existing.job_type = profile.job_type
    existing.required_skills = profile.required_skills
    existing.preferred_skills = profile.preferred_skills
    existing.responsibilities = profile.responsibilities
    existing.requirements = profile.requirements
    existing.risk_tags = profile.risk_tags
    existing.analysis_json = profile.model_dump()
    db.commit()


@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    """删除单个岗位；级联清理 job_analysis 与不带 task_id 的 match_results。"""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "岗位不存在")
    db.query(JobAnalysis).filter(JobAnalysis.job_id == job_id).delete()
    db.query(MatchResult).filter(
        MatchResult.job_id == job_id, MatchResult.task_id.is_(None)
    ).delete(synchronize_session=False)
    db.delete(job)
    db.commit()
    return {"ok": True, "id": job_id}


@router.delete("")
def batch_delete_jobs(
    ids: str = Query(..., description="逗号分隔的岗位 ID 列表，如 ids=1,2,3"),
    db: Session = Depends(get_db),
):
    """批量删除岗位。"""
    try:
        id_list = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "ids 参数格式错误，应为逗号分隔的整数")
    if not id_list:
        raise HTTPException(400, "ids 为空")
    found_ids = [r.id for r in db.query(Job).filter(Job.id.in_(id_list)).all()]
    if not found_ids:
        raise HTTPException(404, "未找到任何匹配的岗位")
    db.query(JobAnalysis).filter(JobAnalysis.job_id.in_(found_ids)).delete(
        synchronize_session=False
    )
    db.query(MatchResult).filter(
        MatchResult.job_id.in_(found_ids), MatchResult.task_id.is_(None)
    ).delete(synchronize_session=False)
    db.query(Job).filter(Job.id.in_(found_ids)).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "deleted": found_ids}


class _AnalyzeModeUpdate(BaseModel):
    analyze_mode: str


_FULL_MODE_LIMIT = 10  # 全文模式一次最多 10 个岗位


@router.put("/{job_id}/analyze-mode", response_model=JobOut)
def set_analyze_mode(
    job_id: int, req: _AnalyzeModeUpdate, db: Session = Depends(get_db)
):
    """设置单条岗位的分析模式。全文模式会被前端校验 N<=10 限制（后端也兜底一次）。"""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "岗位不存在")
    mode = (req.analyze_mode or "summary").lower()
    if mode not in ("summary", "full"):
        raise HTTPException(400, "analyze_mode 必须是 summary 或 full")
    job.analyze_mode = mode
    db.commit()
    db.refresh(job)
    return _to_out(job, db)


@router.get("/full-mode/count")
def count_full_mode(db: Session = Depends(get_db)):
    """全文模式岗位数（含上限），用于前端校验。"""
    n = db.query(Job).filter(Job.analyze_mode == "full").count()
    return {"count": n, "limit": _FULL_MODE_LIMIT}


class _BatchAnalyzeItem(BaseModel):
    id: int
    ok: bool
    error: str = ""


class _BatchAnalyzeRequest(BaseModel):
    ids: list[int]


@router.post("/analyze-batch")
def analyze_batch(
    req: _BatchAnalyzeRequest, db: Session = Depends(get_db)
) -> list[_BatchAnalyzeItem]:
    """批量重新解析岗位。LLM 调用并发跑（限 LLM_CONCURRENCY）。"""
    settings = get_settings()
    ids = list(dict.fromkeys(req.ids))  # 去重保序
    if not ids:
        return []
    jobs = {j.id: j for j in db.query(Job).filter(Job.id.in_(ids)).all()}
    max_c = max(1, min(settings.llm_concurrency, len(ids)))
    results: dict[int, _BatchAnalyzeItem] = {jid: _BatchAnalyzeItem(id=jid, ok=False, error="未处理") for jid in ids}

    def _do(jid: int):
        job = jobs.get(jid)
        if job is None:
            return jid, None, "岗位不存在"
        hints = {
            "company_name": job.company_name,
            "job_title": job.job_title,
            "city": job.city,
            "salary": job.salary,
        }
        try:
            profile = job_agent.run(job.jd_text, hints)
            return jid, profile, None
        except Exception as e:  # noqa: BLE001
            return jid, None, str(e)

    with ThreadPoolExecutor(max_workers=max_c) as pool:
        futures = {pool.submit(_do, jid): jid for jid in ids}
        for fut in as_completed(futures):
            jid, profile, err = fut.result()
            if err is not None:
                results[jid] = _BatchAnalyzeItem(id=jid, ok=False, error=err)
                continue
            try:
                job = jobs[jid]
                _apply_analysis(job, profile, db)
                results[jid] = _BatchAnalyzeItem(id=jid, ok=True)
            except Exception as e:  # noqa: BLE001
                results[jid] = _BatchAnalyzeItem(id=jid, ok=False, error=str(e))
    return [results[i] for i in ids]
