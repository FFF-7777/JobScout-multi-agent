"""岗位相关接口。"""
from __future__ import annotations

import re

_MAX_UPLOAD = 10 * 1024 * 1024  # 10 MiB 上传上限

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from database import get_db
from models import Job, JobAnalysis
from schemas.job import JobImportTextRequest, JobImportUrlRequest, JobOut
from services import document_parser, job_agent, url_fetcher

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_SPLIT_RE = re.compile(r"\n\s*(?:-{3,}|={3,}|#{2,}|\n)\s*\n")


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
    job.company_name = job.company_name or profile.company_name
    job.job_title = job.job_title or profile.job_title
    job.city = job.city or profile.city
    job.salary = job.salary or profile.salary

    existing = db.query(JobAnalysis).filter(JobAnalysis.job_id == job_id).first()
    if existing is None:
        existing = JobAnalysis(job_id=job_id)
        db.add(existing)
    existing.job_type = profile.job_type
    existing.required_skills = profile.required_skills
    existing.preferred_skills = profile.preferred_skills
    existing.responsibilities = profile.responsibilities
    existing.requirements = profile.requirements
    existing.risk_tags = profile.risk_tags
    existing.analysis_json = profile.model_dump()
    db.commit()
    db.refresh(job)
    return _to_out(job, db)
