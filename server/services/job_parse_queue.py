"""后台岗位解析队列（P1#9）。

代替 threading.Thread 直接调 _background_parse 的方式——导入接口把任务写入
job_parse_tasks 表，一个 daemon 线程轮询并触发解析。服务重启时自动捡回
卡在 queued/running 的任务，不再"永远 parsing"。
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from config import get_settings
from database import SessionLocal
from models import Job, JobAnalysis, JobParseTask
from services import job_agent
from services.jd_preprocessor import clean_ocr_jd

_logger = logging.getLogger(__name__)

_POLL_INTERVAL = 0.5  # 秒
_daemon_started = False
_stop_event = threading.Event()
_consecutive_errors = 0
_MAX_CONSECUTIVE_ERRORS = 10


def _utcnow() -> datetime:
    """tz 安全的 naive UTC 时间（避免 datetime.utcnow() 的 DeprecationWarning）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enqueue_parse(job_ids: list[int]) -> None:
    """把岗位加入后台解析队列。"""
    if not job_ids:
        return
    db = SessionLocal()
    try:
        for jid in job_ids:
            db.add(JobParseTask(job_id=jid, status="queued"))
        db.commit()
    finally:
        db.close()


def _recover_stale_tasks() -> None:
    """启动时把 stuck 在 queued/running 的任务重置为 queued，让 daemon 重新调度。"""
    db = SessionLocal()
    try:
        rows = db.query(JobParseTask).filter(
            JobParseTask.status.in_(["queued", "running"])
        ).all()
        for r in rows:
            r.status = "queued"
            r.error_message = ""
        if rows:
            db.commit()
            print(f"[job_parse_queue] 重置 {len(rows)} 个遗留解析任务")
    finally:
        db.close()


def _daemon_loop() -> None:
    """后台轮询队列，自动解析待处理的岗位。
    
    带连续错误检测与退避：连续 N 次异常后暂停 60s 再重试，
    避免（如 DB 断连）时疯狂刷错误日志。
    """
    global _consecutive_errors
    while not _stop_event.is_set():
        try:
            _process_batch()
            _consecutive_errors = 0
        except Exception:  # noqa: BLE001
            _consecutive_errors += 1
            _logger.warning(
                "job_parse_queue daemon error (#%d consecutive)",
                _consecutive_errors, exc_info=True,
            )
            if _consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                _logger.error(
                    "job_parse_queue daemon: %d consecutive errors, "
                    "pausing 60s before retry",
                    _consecutive_errors,
                )
                time.sleep(60)
                _consecutive_errors = 0
        # 用 wait 替代 sleep，支持 stop_event 立即退出
        _stop_event.wait(_POLL_INTERVAL)


def _process_batch() -> None:
    """取一批 queued 任务并发解析。"""
    db = SessionLocal()
    try:
        rows = db.query(JobParseTask).filter(
            JobParseTask.status == "queued"
        ).order_by(JobParseTask.id.asc()).limit(20).all()
        if not rows:
            return
        for r in rows:
            r.status = "running"
        db.commit()
        task_ids = [r.id for r in rows]
        job_ids = [r.job_id for r in rows]
    finally:
        db.close()

    settings = get_settings()
    max_c = max(1, min(settings.job_agent_concurrency, len(job_ids)))
    with ThreadPoolExecutor(max_workers=max_c) as pool:
        list(pool.map(_parse_single, [(jid, tid) for jid, tid in zip(job_ids, task_ids)]))


def _parse_single(args: tuple[int, int]) -> None:
    """解析单岗位。"""
    jid, task_id = args
    error = ""
    try:
        _do_parse(jid)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:500]

    db = SessionLocal()
    try:
        row = db.get(JobParseTask, task_id)
        if row is None:
            return
        if error:
            row.status = "failed"
            row.error_message = error
        else:
            row.status = "done"
        row.finished_at = _utcnow()
        db.commit()
    finally:
        db.close()


def _do_parse(jid: int) -> None:
    """调用 Job Agent 解析单个岗位，结果写回 jobs / job_analysis。"""
    from routers.jobs import _apply_analysis

    s = SessionLocal()
    try:
        job = s.get(Job, jid)
        if job is None:
            return
        hints = {
            "company_name": job.company_name,
            "job_title": job.job_title,
            "city": job.city,
            "salary": job.salary,
        }
        profile = job_agent.run(job.jd_text, hints, source=job.source)
        _apply_analysis(job, profile, s)
        s.commit()
    except Exception:  # noqa: BLE001
        s.rollback()
        # 把真实异常传播到 _parse_single 的 except 里
        raise
    finally:
        s.close()


def start_parse_daemon() -> None:
    """启动一次后台解析 daemon（幂等）。
    
    注意：必须用 daemon=False，否则 _stop_event 来不及触发就被强制终止。
    服务退出时 lifespan shutdown 阶段调用 stop_parse_daemon() 优雅退出。
    """
    global _daemon_started
    if _daemon_started:
        return
    _daemon_started = True
    _recover_stale_tasks()
    t = threading.Thread(target=_daemon_loop, daemon=False)
    t.start()
    _logger.info("后台解析 daemon 已启动")


def stop_parse_daemon(timeout: float = 10.0) -> None:
    """优雅退出后台解析 daemon：设 stop 信号，等待当前批次完成后线程结束。"""
    global _daemon_started
    if not _daemon_started:
        return
    _stop_event.set()
    _daemon_started = False
    _logger.info("后台解析 daemon 已发送停止信号（timeout=%.1fs）", timeout)
