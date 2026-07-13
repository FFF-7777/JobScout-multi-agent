"""岗位相关接口。"""
from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models import AgentItemRun, Job, JobAnalysis, JobParseTask, JobReport, MatchResult
from schemas.job import (
    JobImportTextRequest,
    JobImportUrlRequest,
    JobOut,
    ImportImagesResult,
    ImportImageFailed,
)
from services import baidu_ocr, document_parser, job_agent, url_fetcher
from services.jd_preprocessor import clean_ocr_jd
from services.job_parse_queue import enqueue_parse

logger = logging.getLogger(__name__)

# 延迟导入 OCR 服务商模块，运行时根据 settings.ocr_provider 选择
def _get_ocr_service():
    from config import get_settings

    provider = (get_settings().ocr_provider or "baidu").lower()
    if provider == "tencent":
        from services import tencent_ocr

        return tencent_ocr, "tencent"
    return baidu_ocr, "baidu"

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_SUPPORTED_IMPORT_HOSTS = {"zhaopin.com", "www.zhaopin.com"}
_GUIDED_IMPORT_HOSTS = {
    "zhipin.com": "BOSS直聘",
    "www.zhipin.com": "BOSS直聘",
    "lagou.com": "拉勾",
    "www.lagou.com": "拉勾",
    "liepin.com": "猎聘",
    "www.liepin.com": "猎聘",
    "m.liepin.com": "猎聘",
    "51job.com": "前程无忧 51job",
    "www.51job.com": "前程无忧 51job",
    "jobs.51job.com": "前程无忧 51job",
}


def _job_url_import_error(platform: str | None = None) -> str:
    if platform:
        return (
            f"当前仅稳定支持智联招聘链接导入；{platform} 链接受验证码、登录态或反爬限制影响，"
            "请改用“粘贴 JD”或“截图 OCR 导入”。"
        )
    return "当前仅稳定支持智联招聘链接导入，请改用智联招聘链接，或使用“粘贴 JD / 截图 OCR 导入”。"


def _validate_import_url_host(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host in _SUPPORTED_IMPORT_HOSTS:
        return
    if host in _GUIDED_IMPORT_HOSTS:
        raise HTTPException(400, _job_url_import_error(_GUIDED_IMPORT_HOSTS[host]))
    raise HTTPException(400, _job_url_import_error())


def _parse_job_by_id(job_id: int) -> None:
    """在独立数据库会话中解析单个岗位（供后台 daemon 线程调用）。

    整个「标记解析中 → LLM 解析 → 文本清洗 → 字段写回 → 数据库提交」
    全部包在异常处理里，任何一步失败都标记 parse_status=failed 并写入
    「异常类型: 真实消息」便于定位问题来源。
    """
    from database import SessionLocal

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return

        job.parse_status = "parsing"
        job.parse_error = ""
        db.commit()

        hints = {
            "company_name": job.company_name,
            "job_title": job.job_title,
            "city": job.city,
            "salary": job.salary,
        }
        profile = job_agent.run(job.jd_text, hints, source=job.source, model_role="fast")
        _apply_analysis(job, profile, db)  # 内部已 commit
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("岗位自动解析失败，job_id=%s", job_id)
        # 重新获取，避免使用异常状态中的 ORM 对象
        job = db.get(Job, job_id)
        if job is not None:
            job.parse_status = "failed"
            job.parse_error = f"{type(exc).__name__}: {exc}"[:500]
            db.commit()
    finally:
        db.close()


def _background_parse(job_ids: list[int]) -> None:
    """(已弃用，改用 job_parse_queue) 导入后自动解析。"""
    from services.job_parse_queue import enqueue_parse

    enqueue_parse(job_ids)

_SPLIT_RE = re.compile(r"\n\s*(?:-{3,}|={3,}|#{2,}|\n)\s*\n")
_MAX_UPLOAD = 10 * 1024 * 1024  # 10 MiB 上传上限
_MAX_IMAGES = 20  # 单次最多上传图片数
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/bmp", "image/webp"}


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
    created_ids: list[int] = []
    for chunk in chunks:
        job = Job(source="manual", jd_text=chunk)
        db.add(job)
        db.commit()
        db.refresh(job)
        # 标记解析中，交给后台自动解析
        job.parse_status = "parsing"
        db.commit()
        created.append(_to_out(job, db))
        created_ids.append(job.id)
    # 导入后自动解析（不阻塞返回）
    enqueue_parse(created_ids)
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
    created_ids: list[int] = []
    for row in rows:
        job = Job(**row)
        db.add(job)
        db.commit()
        db.refresh(job)
        job.parse_status = "parsing"
        db.commit()
        created.append(_to_out(job, db))
        created_ids.append(job.id)
    enqueue_parse(created_ids)
    return created


@router.post("/import-url", response_model=JobOut)
def import_url(req: JobImportUrlRequest, db: Session = Depends(get_db)):
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "链接为空")
    _validate_import_url_host(url)
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
    job.parse_status = "parsing"
    db.commit()
    created_id = job.id
    out = _to_out(job, db)
    enqueue_parse([created_id])
    return out


@router.post("/import-images", response_model=ImportImagesResult)
async def import_images(
    files: list[UploadFile] = File(...), db: Session = Depends(get_db)
):
    """通过 OCR 识别图片中的 JD 文本并导入岗位（服务商由 OCR_PROVIDER 决定：baidu/tencent）。

    支持 PNG / JPEG / JPG / BMP / WebP，单次最多 20 张，每张上限 10MB。
    返回成功创建的岗位列表 + 逐张失败明细（含文件名与原因）。
    """
    if not files:
        raise HTTPException(400, "未上传任何文件")
    if len(files) > _MAX_IMAGES:
        raise HTTPException(400, f"单次最多上传 {_MAX_IMAGES} 张图片")

    # 校验 MIME 类型 + 大小
    images: list[tuple[bytes, str]] = []
    for f in files:
        mime = (f.content_type or "").lower()
        if mime not in _IMAGE_MIMES:
            raise HTTPException(400, f"不支持的文件格式：{f.filename or 'unknown'}（仅支持图片）")
        data = await f.read()
        if len(data) > _MAX_UPLOAD:
            raise HTTPException(413, f"文件 {f.filename} 过大（上限 {_MAX_UPLOAD // 1024 // 1024} MB）")
        images.append((data, f.filename or "image"))

    # 根据 OCR_PROVIDER 选择服务商（百度 / 腾讯）批量识别
    settings = get_settings()
    ocr_service, provider = _get_ocr_service()
    try:
        ocr_results = await ocr_service.recognize_images_batch(images)
    except ValueError as e:
        raise HTTPException(400, f"OCR 识别失败：{e}") from e

    created: list[dict] = []
    failed: list[ImportImageFailed] = []
    created_ids: list[int] = []

    for res in ocr_results:
        fname = res.get("filename", "")
        if "error" in res:
            failed.append(ImportImageFailed(filename=fname, error=res["error"]))
            continue
        text = (res.get("text") or "").strip()
        if not text:
            failed.append(ImportImageFailed(filename=fname, error="未识别到文字"))
            continue
        job = Job(source="ocr_image", jd_text=text[:30000])
        db.add(job)
        db.commit()
        db.refresh(job)
        job.parse_status = "parsing"
        db.commit()
        created.append(_to_out(job, db))
        created_ids.append(job.id)

    # 全部失败：明确报错
    if not created and failed:
        detail = "；".join(f"{f.filename}: {f.error}" for f in failed[:5])
        raise HTTPException(422, f"所有图片识别均失败：{detail}")

    # OCR 导入后自动解析（不阻塞返回）
    enqueue_parse(created_ids)
    return ImportImagesResult(created=created, failed=failed)


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
        profile = job_agent.run(job.jd_text, hints, source=job.source)
    except Exception as exc:
        job.parse_status = "failed"
        job.parse_error = str(exc)[:500]
        db.commit()
        raise HTTPException(502, f"岗位分析失败：{str(exc)[:200]}")
    _apply_analysis(job, profile, db)
    db.refresh(job)
    return _to_out(job, db)


def _apply_analysis(job: Job, profile, db: Session) -> None:
    """将 JobProfile 逐字段写入 job 主表与 job_analysis 表。

    parse_status=success 在全部字段写入后设置，避免出现「状态成功但数据未保存」。
    """
    # 已有表格字段优先保留；为空时用 Agent 结果补充
    job.company_name = job.company_name or profile.company_name
    job.job_title = job.job_title or profile.job_title
    job.city = job.city or profile.city
    job.salary = job.salary or profile.salary
    job.education = job.education or profile.education
    job.experience = job.experience or profile.experience
    # 列表预览摘要（LLM jd_summary，为空时 job_agent 已用结构化字段兜底拼接）
    job.jd_summary = profile.jd_summary or ""
    # 字段写入后，再外部分析表
    existing = db.query(JobAnalysis).filter(JobAnalysis.job_id == job.id).first()
    if existing is None:
        existing = JobAnalysis(job_id=job.id)
        db.add(existing)
        try:
            db.flush()  # 触发 INSERT，让唯一约束尽早暴露
        except Exception:
            db.rollback()
            existing = db.query(JobAnalysis).filter(JobAnalysis.job_id == job.id).first()
            if existing is None:
                raise  # 真异常，向上传播
    existing.job_type = profile.job_type
    existing.required_skills = profile.required_skills
    existing.preferred_skills = profile.preferred_skills
    existing.responsibilities = profile.responsibilities
    existing.requirements = profile.requirements
    existing.risk_tags = profile.risk_tags
    existing.analysis_json = profile.model_dump()
    # OCR 来源：保留清洗后正文
    if job.source == "ocr_image":
        job.cleaned_jd_text = clean_ocr_jd(job.jd_text)
    else:
        job.cleaned_jd_text = job.cleaned_jd_text or job.jd_text
    # 所有字段写入成功后，最终标记 success
    job.parse_status = "success"
    job.parse_error = ""
    db.commit()


@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    """删除单个岗位及其解析任务、匹配结果和单岗位报告。"""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "岗位不存在")
    result_ids = [r.id for r in db.query(MatchResult.id).filter(MatchResult.job_id == job_id)]
    if result_ids:
        db.query(JobReport).filter(JobReport.match_result_id.in_(result_ids)).delete(
            synchronize_session=False
        )
    db.query(AgentItemRun).filter(
        AgentItemRun.agent_name == "Match Agent", AgentItemRun.item_id == job_id
    ).delete(synchronize_session=False)
    db.query(JobParseTask).filter(JobParseTask.job_id == job_id).delete(synchronize_session=False)
    db.query(JobAnalysis).filter(JobAnalysis.job_id == job_id).delete(synchronize_session=False)
    db.query(MatchResult).filter(MatchResult.job_id == job_id).delete(synchronize_session=False)
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
    result_ids = [
        r.id for r in db.query(MatchResult.id).filter(MatchResult.job_id.in_(found_ids))
    ]
    if result_ids:
        db.query(JobReport).filter(JobReport.match_result_id.in_(result_ids)).delete(
            synchronize_session=False
        )
    db.query(AgentItemRun).filter(
        AgentItemRun.agent_name == "Match Agent", AgentItemRun.item_id.in_(found_ids)
    ).delete(synchronize_session=False)
    db.query(JobParseTask).filter(JobParseTask.job_id.in_(found_ids)).delete(
        synchronize_session=False
    )
    db.query(MatchResult).filter(MatchResult.job_id.in_(found_ids)).delete(
        synchronize_session=False
    )
    db.query(Job).filter(Job.id.in_(found_ids)).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "deleted": found_ids}


class _AnalyzeModeUpdate(BaseModel):
    analyze_mode: str


# 深度分析岗位无硬性数量上限（config.full_mode_limit=0）。
# 单条 set_analyze_mode 接口、启动任务时的 full_count 校验都由 config 统一控制。
# 此处保留注释，标记历史常量已弃用。


@router.put("/{job_id}/analyze-mode", response_model=JobOut)
def set_analyze_mode(
    job_id: int, req: _AnalyzeModeUpdate, db: Session = Depends(get_db)
):
    """设置单条岗位的分析模式。深度分析无硬性数量上限（受 LLM 成本与耗时影响）。"""
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
    """深度分析岗位数（参考用，无硬性上限）。"""
    n = db.query(Job).filter(Job.analyze_mode == "full").count()
    return {"count": n, "limit": get_settings().full_mode_limit}


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
            profile = job_agent.run(job.jd_text, hints, source=job.source)
            return jid, profile, None
        except Exception as e:  # noqa: BLE001
            return jid, None, str(e)

    with ThreadPoolExecutor(max_workers=max_c) as pool:
        futures = {pool.submit(_do, jid): jid for jid in ids}
        for fut in as_completed(futures):
            jid, profile, err = fut.result()
            job = jobs.get(jid)
            if err is not None:
                if job is not None:
                    job.parse_status = "failed"
                    job.parse_error = str(err)[:500]
                    db.commit()
                results[jid] = _BatchAnalyzeItem(id=jid, ok=False, error=str(err))
                continue
            try:
                if job is None:
                    raise ValueError("岗位不存在")
                _apply_analysis(job, profile, db)  # 内部已 commit + 标记 success
                results[jid] = _BatchAnalyzeItem(id=jid, ok=True)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                job = db.get(Job, jid)
                if job is not None:
                    job.parse_status = "failed"
                    job.parse_error = f"{type(exc).__name__}: {exc}"[:500]
                    db.commit()
                results[jid] = _BatchAnalyzeItem(id=jid, ok=False, error=str(exc))
    return [results[i] for i in ids]
