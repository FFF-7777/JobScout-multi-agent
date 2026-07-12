"""简历相关接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

_MAX_UPLOAD = 10 * 1024 * 1024  # 10 MiB 上传上限
from sqlalchemy.orm import Session

from database import get_db
from models import Resume
from schemas.resume import (
    ProfileUpdateRequest,
    ResumeOut,
    ResumeParseRequest,
)
from services import document_parser, resume_agent

router = APIRouter(prefix="/api/resumes", tags=["resumes"])


@router.post("/upload", response_model=ResumeOut)
async def upload_resume(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    data = await file.read()
    if len(data) > _MAX_UPLOAD:
        raise HTTPException(413, f"文件过大（上限 {_MAX_UPLOAD // 1024 // 1024} MiB）")
    text = document_parser.parse_resume_bytes(file.filename or "resume", data)
    if not text.strip():
        raise HTTPException(400, "无法从文件中提取到文本内容")
    resume = Resume(filename=file.filename or "resume", raw_text=text)
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


@router.post("/parse", response_model=ResumeOut)
def parse_resume(req: ResumeParseRequest, db: Session = Depends(get_db)):
    """粘贴文本 -> 保存并生成画像。"""
    if not req.text.strip():
        raise HTTPException(400, "简历文本为空")
    resume = Resume(filename=req.filename, raw_text=req.text)
    profile = resume_agent.run(req.text)
    resume.profile_json = profile.model_dump()
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


@router.post("/{resume_id}/parse", response_model=ResumeOut)
def parse_existing(resume_id: int, db: Session = Depends(get_db)):
    """对已上传的简历触发画像解析。"""
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(404, "简历不存在")
    profile = resume_agent.run(resume.raw_text)
    resume.profile_json = profile.model_dump()
    db.commit()
    db.refresh(resume)
    return resume


@router.get("", response_model=list[ResumeOut])
def list_resumes(db: Session = Depends(get_db)):
    return db.query(Resume).order_by(Resume.id.desc()).all()


@router.get("/{resume_id}", response_model=ResumeOut)
def get_resume(resume_id: int, db: Session = Depends(get_db)):
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(404, "简历不存在")
    return resume


@router.put("/{resume_id}/profile", response_model=ResumeOut)
def update_profile(
    resume_id: int, req: ProfileUpdateRequest, db: Session = Depends(get_db)
):
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(404, "简历不存在")
    resume.profile_json = req.profile_json.model_dump()
    db.commit()
    db.refresh(resume)
    return resume
