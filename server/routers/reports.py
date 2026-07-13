"""报告接口：查询、按需生成、Markdown 下载、Excel 导出。"""
from __future__ import annotations

import io
import threading
import uuid
from datetime import datetime, timezone


def _utcnow() -> datetime:
    """tz 安全的 naive UTC 时间（避免 datetime.utcnow() 的 DeprecationWarning）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import get_settings
from database import SessionLocal, get_db
from models import (
    AgentItemRun,
    Job,
    JobAnalysis,
    JobReport,
    MatchResult,
    Report,
    ReportTask,
    Resume,
)
from schemas.job import JobProfile
from schemas.match import MatchResultModel
from schemas.report import ReportOut
from schemas.resume import ResumeProfile
from services import report_agent
from services.concurrency import bounded_map
from services.item_run import upsert_item_run

router = APIRouter(prefix="/api/reports", tags=["reports"])


class ReportGenerateRequest(BaseModel):
    match_result_ids: list[int] = Field(default_factory=list)
    # standard：代码模板，立即生成，不调 LLM；deep：调 LLM 生成深度报告
    mode: str = "standard"


@router.post("/generate-batch")
def generate_reports_batch(req: ReportGenerateRequest, db: Session = Depends(get_db)):
    """按需为选中的匹配结果生成报告。

    - standard：纯代码模板（匹配点 / 缺口 / 风险 / 面试重点），**同步**立即返回，无 LLM 成本。
    - deep：调用 Report Agent（LLM）生成含面试题 / BOSS 话术等的深度报告。
      **异步**：立即返回 task_id，后台线程生成，前端轮询 GET /api/reports/tasks/{task_id}
      看进度、GET /api/reports/tasks/{task_id}/items 看单岗位状态，避免前端 Axios 120s 超时。
    """
    if not req.match_result_ids:
        raise HTTPException(400, "match_result_ids 不能为空")
    mode = (req.mode or "standard").lower()
    if mode not in ("standard", "deep"):
        raise HTTPException(400, "mode 仅支持 standard / deep")

    # 基础报告（代码模板）：同步即时返回，无 LLM 成本
    if mode == "standard":
        payloads = _build_payloads(db, req.match_result_ids)
        if not payloads:
            raise HTTPException(400, "无可生成报告的匹配结果（缺少简历画像或匹配详情）")
        model = get_settings().llm_fast_model or get_settings().llm_model
        generated = 0
        cache_hits = 0
        errors: list[dict] = []
        for p in payloads:
            key = _report_key(p, model, mode)
            if _report_cache_hit(p["result_id"], key):
                cache_hits += 1
                generated += 1
                continue
            try:
                report = report_agent.build_standard_job_report(p["job_profile"], p["match"])
                _persist_report(p["result_id"], report, key)
                generated += 1
            except Exception as e:  # noqa: BLE001
                errors.append({"result_id": p["result_id"], "error": str(e)})
        return {
            "mode": mode,
            "requested": len(req.match_result_ids),
            "generated": generated,
            "cache_hits": cache_hits,
            "errors": errors,
        }

    # deep 报告：先校验可生成，再立即返回 task_id，后台线程跑 LLM
    payloads = _build_payloads(db, req.match_result_ids)
    if not payloads:
        raise HTTPException(400, "无可生成报告的匹配结果（缺少简历画像或匹配详情）")
    task_id = uuid.uuid4().hex
    rt = ReportTask(task_id=task_id, mode=mode, status="queued", total=len(payloads))
    db.add(rt)
    db.commit()
    threading.Thread(
        target=_run_report_task, args=(task_id, req.match_result_ids, mode), daemon=True
    ).start()
    return {
        "task_id": task_id,
        "status": "queued",
        "total_items": len(payloads),
        "mode": mode,
    }


# ---- deep 报告后台任务辅助（P0#6）----


def _build_payloads(db: Session, match_result_ids: list[int]) -> list[dict]:
    """按 match_result_ids 预加载生成报告所需数据，返回 payload 列表。"""
    rows = db.query(MatchResult).filter(MatchResult.id.in_(match_result_ids)).all()
    if not rows:
        return []
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
                "item_label": f"{job.company_name or ''} {job.job_title or ''}".strip(),
            }
        )
    return payloads


def _report_key(p: dict, model_role_model: str, mode: str) -> str:
    return report_agent.build_report_cache_key(
        p["resume_profile"].model_dump(),
        p["job_profile"].model_dump(),
        p["match"].model_dump(),
        model_role_model,
        mode,
    )


def _report_cache_hit(result_id: int, key: str) -> bool:
    """检查报告是否已缓存：优先查 job_reports 表（P1#12），fallback 到 detail_json。"""
    # P1#12：新路径 → job_reports
    s = SessionLocal()
    try:
        existing = (
            s.query(JobReport)
            .filter(JobReport.match_result_id == result_id, JobReport.cache_key == key)
            .first()
        )
        if existing and existing.report_json:
            return True
    finally:
        s.close()
    # 旧路径 → detail_json（兼容迁移前数据）
    s = SessionLocal()
    try:
        row = s.get(MatchResult, result_id)
        return bool(
            row
            and row.report_cache_key == key
            and row.detail_json
            and row.detail_json.get("report")
        )
    finally:
        s.close()


def _persist_report(result_id: int, report_dict: dict, key: str) -> None:
    """把报告写入 job_reports 表（P1#12），并同步写入 detail_json 兼容旧前端。"""
    s = SessionLocal()
    try:
        # 新路径：job_reports 独立行（每 match_result + mode 一条）
        existing = (
            s.query(JobReport)
            .filter(
                JobReport.match_result_id == result_id,
                JobReport.cache_key == key,
            )
            .first()
        )
        if existing is None:
            existing = JobReport(
                match_result_id=result_id,
                mode=report_dict.get("mode", "standard"),
                cache_key=key,
            )
            s.add(existing)
        existing.report_json = report_dict
        existing.cache_key = key
        existing.mode = report_dict.get("mode", "standard")

        # 旧路径：detail_json（兼容旧前端）
        row = s.get(MatchResult, result_id)
        if row and row.detail_json is not None:
            detail = dict(row.detail_json)
            detail["report"] = report_dict
            row.detail_json = detail
            row.report_cache_key = key
        s.commit()
    finally:
        s.close()


def _prune_history_reports() -> None:
    """保留最新的 report_history_limit 份报告，超出最旧的自动删除。

    按 id 降序保留前 N 份（最新），删除其余。每次生成新报告后调用一次。
    """
    limit = get_settings().report_history_limit
    if limit <= 0:
        return
    s = SessionLocal()
    try:
        keep_ids = [
            r.id
            for r in s.query(Report).order_by(Report.id.desc()).limit(limit).all()
        ]
        if not keep_ids:
            return
        s.query(Report).filter(~Report.id.in_(keep_ids)).delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()


def _tick_report_task(
    task_id: str,
    *,
    total: int | None = None,
    done: int = 0,
    failed: int = 0,
    status: str | None = None,
) -> None:
    """原子累加 done/failed，可选更新 total/status。后台线程每完成一条调一次。"""
    s = SessionLocal()
    try:
        rt = s.query(ReportTask).filter(ReportTask.task_id == task_id).first()
        if rt is None:
            return
        if total is not None:
            rt.total = total
        if status is not None:
            rt.status = status
        if done:
            rt.done += done
        if failed:
            rt.failed += failed
        s.commit()
    finally:
        s.close()


def _finish_report_task(task_id: str, *, status: str | None = None) -> None:
    """结束报告任务：默认按 done/failed 判定 done / partial。"""
    s = SessionLocal()
    try:
        rt = s.query(ReportTask).filter(ReportTask.task_id == task_id).first()
        if rt is None:
            return
        if status is None:
            status = "partial" if rt.failed > 0 else "done"
        rt.status = status
        rt.finished_at = _utcnow()
        s.commit()
    finally:
        s.close()


def _run_report_task(task_id: str, match_result_ids: list[int], mode: str) -> None:
    """后台生成 deep 报告（P0#6）：在独立线程里跑 LLM，每完成一条就 tick 进度，
    结束写 finished_at。前端轮询 GET /api/reports/tasks/{task_id} 看进度。
    """
    # 后台线程自建会话加载 payload（请求作用域的 db 已随响应关闭）
    tdb = SessionLocal()
    try:
        payloads = _build_payloads(tdb, match_result_ids)
    finally:
        tdb.close()

    if not payloads:
        _finish_report_task(task_id, status="done")
        return

    _tick_report_task(task_id, total=len(payloads), status="running")
    started = _utcnow()

    # 单写者：仅本后台线程写 AgentItemRun，先把每条标记为 running
    for p in payloads:
        upsert_item_run(
            task_id,
            "Report Agent",
            p["result_id"],
            p["item_label"],
            status="running",
            started_at=started,
        )

    model = get_settings().llm_reasoning_model or get_settings().llm_model

    def _deep_worker(p):
        key = _report_key(p, model, mode)
        if _report_cache_hit(p["result_id"], key):
            return p, None, key, True
        rep = report_agent.run(
            p["resume_profile"],
            p["job_profile"],
            p["match"],
            resume_text=p["resume_text"][:8000] if p["resume_text"] else None,
            model_role="report",
        )
        return p, rep, key, False

    def _deep_on_result(p, payload, err):
        if err is not None or payload is None:
            _tick_report_task(task_id, failed=1)
            upsert_item_run(
                task_id,
                "Report Agent",
                p["result_id"],
                p["item_label"],
                status="failed",
                error=str(err)[:500],
                finished_at=_utcnow(),
                duration_ms=int((_utcnow() - started).total_seconds() * 1000),
            )
            return
        _, rep, key, hit = payload
        if hit:
            _tick_report_task(task_id, done=1)
            upsert_item_run(
                task_id,
                "Report Agent",
                p["result_id"],
                p["item_label"],
                status="done",
                finished_at=_utcnow(),
                duration_ms=int((_utcnow() - started).total_seconds() * 1000),
            )
            return
        _persist_report(p["result_id"], rep.model_dump(), key)
        _tick_report_task(task_id, done=1)
        upsert_item_run(
            task_id,
            "Report Agent",
            p["result_id"],
            p["item_label"],
            status="done",
            finished_at=_utcnow(),
            duration_ms=int((_utcnow() - started).total_seconds() * 1000),
        )

    try:
        bounded_map(
            payloads,
            _deep_worker,
            max_concurrency=get_settings().report_agent_concurrency,
            on_result=_deep_on_result,
        )
    except Exception:  # noqa: BLE001
        _finish_report_task(task_id, status="partial")
        return

    _finish_report_task(task_id)

    # Deep 报告完成后，重新聚合 Report 表的汇总 Markdown
    try:
        _rebuild_report_after_deep(match_result_ids)
    except Exception:  # noqa: BLE001
        pass  # 重建失败不阻塞主线


def _rebuild_report_after_deep(match_result_ids: list[int]) -> None:
    """深度报告生成后，用深度内容重新聚合同 task 的 Report 表 Markdown。"""
    from schemas.job import JobProfile
    from schemas.match import MatchResultModel
    from schemas.resume import ResumeProfile

    s = SessionLocal()
    try:
        # 找到这批 match_result 所属的 task_id
        rows = (
            s.query(MatchResult)
            .filter(MatchResult.id.in_(match_result_ids))
            .all()
        )
        if not rows:
            return
        # 取第一个有 task_id 的（正常情况下同批属同一 task）
        task_id = rows[0].task_id
        if not task_id:
            return

        # 找对应的 Report 行
        report_row = s.query(Report).filter(Report.task_id == task_id).first()
        if report_row is None:
            return

        # 取该 task 下所有 match_result
        all_mrs = (
            s.query(MatchResult)
            .filter(MatchResult.task_id == task_id)
            .order_by(MatchResult.score.desc())
            .all()
        )
        if not all_mrs:
            return

        # 取简历画像
        resume = s.get(Resume, all_mrs[0].resume_id)
        if resume is None or not resume.profile_json:
            return
        resume_profile = ResumeProfile.model_validate(resume.profile_json)

        # 组装 items
        items: list[dict] = []
        for mr in all_mrs:
            job = s.get(Job, mr.job_id)
            if job is None or not mr.detail_json:
                continue
            ja = (
                s.query(JobAnalysis)
                .filter(JobAnalysis.job_id == mr.job_id)
                .first()
            )
            job_profile = JobProfile.model_validate(
                ja.analysis_json
                if ja and ja.analysis_json
                else {
                    "company_name": job.company_name or "",
                    "job_title": job.job_title or "",
                    "city": job.city or "",
                    "salary": job.salary or "",
                }
            )
            match = MatchResultModel.model_validate(mr.detail_json)
            # 优先取 JobReport 表的 deep 报告，fallback 到 detail_json 的 report
            best_report: dict | None = None
            jr = (
                s.query(JobReport)
                .filter(
                    JobReport.match_result_id == mr.id,
                    JobReport.mode == "deep",
                )
                .order_by(JobReport.id.desc())
                .first()
            )
            if jr and jr.report_json:
                best_report = jr.report_json
            else:
                detail = mr.detail_json or {}
                if detail.get("report"):
                    best_report = detail["report"]
            if not best_report:
                continue
            items.append({
                "job": job_profile,
                "match": match,
                "report": best_report,
            })

        if not items:
            return

        # 生成混合 Markdown
        hybrid_md = report_agent.build_hybrid_report_markdown(resume_profile, items)
        has_deep = any((it["report"]).get("mode") == "deep" for it in items)
        deep_count = sum(1 for it in items if (it["report"]).get("mode") == "deep")
        report_row.markdown_content = hybrid_md
        report_row.title = (
            f"岗位分析报告（{len(items)} 个岗位"
            + (f"，{deep_count} 个深度分析" if has_deep else "")
            + f"） - {resume_profile.name or '候选人'}"
        )
        report_row.summary = (
            f"共 {len(items)} 个岗位"
            + (f"，{deep_count} 个含深度分析" if has_deep else "，基础报告")
        )
        s.commit()
    finally:
        s.close()


@router.get("/tasks/{task_id}")
def get_report_task(task_id: str, db: Session = Depends(get_db)):
    """查询 deep 报告后台任务进度（前端轮询：status/done/failed/total）。"""
    rt = db.query(ReportTask).filter(ReportTask.task_id == task_id).first()
    if rt is None:
        raise HTTPException(404, "报告任务不存在")
    return {
        "task_id": rt.task_id,
        "mode": rt.mode,
        "status": rt.status,
        "total": rt.total,
        "done": rt.done,
        "failed": rt.failed,
        "created_at": rt.created_at.isoformat() if rt.created_at else None,
        "finished_at": rt.finished_at.isoformat() if rt.finished_at else None,
    }


@router.get("/tasks/{task_id}/items")
def get_report_task_items(task_id: str, db: Session = Depends(get_db)):
    """该报告任务各岗位的 AgentItemRun 状态（单条成功/失败/错误/耗时）。"""
    rt = db.query(ReportTask).filter(ReportTask.task_id == task_id).first()
    if rt is None:
        raise HTTPException(404, "报告任务不存在")
    rows = (
        db.query(AgentItemRun)
        .filter(AgentItemRun.task_id == task_id, AgentItemRun.agent_name == "Report Agent")
        .order_by(AgentItemRun.id.asc())
        .all()
    )
    return [
        {
            "item_id": r.item_id,
            "item_label": r.item_label,
            "status": r.status,
            "error_message": r.error_message,
            "duration_ms": r.duration_ms,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in rows
    ]


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
