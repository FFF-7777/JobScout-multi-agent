"""简历相关接口。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from database import get_db
from models import JobReport, MatchResult, Report, Resume
from schemas.resume import (
    ProfileUpdateRequest,
    ResumeImportImageFailed,
    ResumeImportImagesResult,
    ResumeOut,
    ResumeParseRequest,
)
from services import document_parser, ocr_pipeline, resume_agent

router = APIRouter(prefix="/api/resumes", tags=["resumes"])

_MAX_UPLOAD = 10 * 1024 * 1024
_MAX_IMAGES = 20
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/bmp", "image/webp"}


def _build_profile(resume: Resume, db: Session, text: str) -> Resume:
    """复用内容哈希缓存；未命中时调用 Resume Agent 生成画像。"""
    h = resume_agent.compute_hash(text)
    if resume.profile_json and resume.content_hash == h:
        return resume
    profile = resume_agent.run(text, model_role="fast")
    resume.profile_json = profile.model_dump()
    resume.content_hash = h
    resume.parsed_at = datetime.utcnow()
    resume.profile_version = (resume.profile_version or 0) + 1
    db.commit()
    return resume


@router.post("/upload", response_model=ResumeOut)
async def upload_resume(file: UploadFile = File(...), db: Session = Depends(get_db)):
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
    resume = _build_profile(resume, db, text)
    db.refresh(resume)
    return resume


@router.post("/import-images", response_model=ResumeImportImagesResult)
async def import_resume_images(
    files: list[UploadFile] = File(...), db: Session = Depends(get_db)
):
    if not files:
        raise HTTPException(400, "未上传任何图片")
    if len(files) > _MAX_IMAGES:
        raise HTTPException(400, f"单次最多上传 {_MAX_IMAGES} 张图片")

    images: list[tuple[bytes, str]] = []
    for f in files:
        mime = (f.content_type or "").lower()
        if mime not in _IMAGE_MIMES:
            raise HTTPException(400, f"不支持的图片格式：{f.filename or 'unknown'}")
        data = await f.read()
        if len(data) > _MAX_UPLOAD:
            raise HTTPException(413, f"文件 {f.filename} 过大（上限 {_MAX_UPLOAD // 1024 // 1024} MB）")
        images.append((data, f.filename or "resume-image"))

    try:
        ocr_results = await ocr_pipeline.recognize_images(images, document_type="resume")
    except ValueError as e:
        raise HTTPException(400, f"OCR 识别失败：{e}") from e

    pages: list[str] = []
    failed: list[ResumeImportImageFailed] = []
    audit_pages: list[dict] = []
    for idx, (_, image_name) in enumerate(images, start=1):
        item = ocr_results[idx - 1] if idx <= len(ocr_results) else {"filename": image_name, "error": "OCR 未返回结果"}
        filename = item.get("filename", f"page-{idx}") or image_name
        text = (item.get("text") or "").strip()
        audit_pages.append({"filename": filename, **(item.get("audit") or {})})
        if not text:
            failed.append(ResumeImportImageFailed(filename=filename, error=str(item.get("error") or "未识别到文字")))
            continue
        if not text:
            failed.append(ResumeImportImageFailed(filename=filename, error="未识别到文字"))
            continue
        pages.append(text)

    if not pages:
        detail = "；".join(f"{f.filename}: {f.error}" for f in failed[:5])
        raise HTTPException(422, f"所有图片识别均失败：{detail or '未识别到可用文字'}")

    merged_text = "\n\n".join(pages)
    first_name = files[0].filename or "resume-image"
    filename = first_name if len(files) == 1 else f"{first_name.rsplit('.', 1)[0]}-images.txt"

    resume = Resume(filename=filename, raw_text=merged_text, ocr_metadata={"pages": audit_pages})
    db.add(resume)
    db.commit()
    db.refresh(resume)
    resume = _build_profile(resume, db, merged_text)
    db.refresh(resume)
    return ResumeImportImagesResult(
        resume=resume,
        total=len(files),
        success=len(pages),
        provider="tencent→baidu→vision",
        failed=failed,
    )


@router.post("/parse", response_model=ResumeOut)
def parse_resume(req: ResumeParseRequest, db: Session = Depends(get_db)):
    if not req.text.strip():
        raise HTTPException(400, "简历文本为空")
    resume = Resume(filename=req.filename, raw_text=req.text)
    db.add(resume)
    db.commit()
    db.refresh(resume)
    resume = _build_profile(resume, db, req.text)
    db.refresh(resume)
    return resume


@router.post("/{resume_id}/parse", response_model=ResumeOut)
def parse_existing(resume_id: int, db: Session = Depends(get_db)):
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(404, "简历不存在")
    resume = _build_profile(resume, db, resume.raw_text)
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


@router.delete("/{resume_id}")
def delete_resume(resume_id: int, db: Session = Depends(get_db)):
    """删除简历、匹配结果和对应报告；保留 Agent 执行历史。"""
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(404, "简历不存在")
    result_ids = [
        r.id for r in db.query(MatchResult.id).filter(MatchResult.resume_id == resume_id)
    ]
    if result_ids:
        db.query(JobReport).filter(JobReport.match_result_id.in_(result_ids)).delete(
            synchronize_session=False
        )
    db.query(MatchResult).filter(MatchResult.resume_id == resume_id).delete(
        synchronize_session=False
    )
    db.query(Report).filter(Report.resume_id == resume_id).delete(synchronize_session=False)
    db.delete(resume)
    db.commit()
    return {"ok": True, "id": resume_id}


@router.get("/summary/list")
def list_resume_summary(db: Session = Depends(get_db)):
    rows = db.query(Resume).order_by(Resume.id.desc()).all()
    out = []
    for r in rows:
        profile = r.profile_json or {}
        out.append(
            {
                "id": r.id,
                "filename": r.filename,
                "profile_name": (profile.get("name") if isinstance(profile, dict) else "") or "",
                "has_profile": bool(profile),
                "created_at": r.created_at,
            }
        )
    return out
