"""LangGraph 工作流：串联四个智能体并持久化执行状态。"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from langgraph.graph import END, StateGraph

from config import get_settings
from database import SessionLocal
from models import AgentRun, Job, JobAnalysis, MatchResult, Report, Resume
from schemas.job import JobProfile
from schemas.match import MatchResultModel
from schemas.report import JobReport
from schemas.resume import ResumeProfile
from services import item_run, job_agent, match_agent, match_core, report_agent, resume_agent
from services.item_run import upsert_item_run
from services.precheck import precheck_job
from services.concurrency import AbortFlow, bounded_map

AGENT_STEPS = [
    ("parse_resume", "Resume Agent"),
    ("parse_jobs", "Job Agent"),
    ("match_jobs", "Match Agent"),
    ("generate_report", "Report Agent"),
]


def _utcnow() -> datetime:
    """返回与数据库字段兼容的无时区 UTC 时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class JobScoutState(TypedDict, total=False):
    task_id: str
    resume_id: int
    job_ids: list[int]
    resume_text: str
    resume_profile: dict
    jobs_raw: list[dict]
    jobs_parsed: list[dict]
    match_results: list[dict]
    final_report: str
    report_id: int
    current_step: str
    errors: list[str]


# --------- agent_runs 钀藉簱宸ュ叿 ---------


def _init_runs(task_id: str) -> None:
    db = SessionLocal()
    try:
        for order, (node, name) in enumerate(AGENT_STEPS, 1):
            existing = (
                db.query(AgentRun)
                .filter(AgentRun.task_id == task_id, AgentRun.agent_name == name)
                .first()
            )
            if existing is None:
                db.add(
                    AgentRun(
                        task_id=task_id,
                        agent_name=name,
                        step_order=order,
                        status="pending",
                    )
                )
        db.commit()
    finally:
        db.close()


def _update_run(
    task_id: str,
    agent_name: str,
    *,
    status: str | None = None,
    summary: str = "",
    output: dict | None = None,
    error: str = "",
    progress: int | None = None,
    eta_seconds: int | None = None,
    eta_low: int | None = None,
    eta_high: int | None = None,
    current_item: str | None = None,
    total_items: int | None = None,
    completed_items: int | None = None,
    failed_items: int | None = None,
    in_flight_items: list | None = None,
    start: bool = False,
    finish: bool = False,
) -> None:
    db = SessionLocal()
    try:
        run = (
            db.query(AgentRun)
            .filter(AgentRun.task_id == task_id, AgentRun.agent_name == agent_name)
            .first()
        )
        if run is None:
            return
        if status is not None:
            run.status = status
        if progress is not None:
            run.progress = progress
        if eta_seconds is not None:
            run.eta_seconds = eta_seconds
        if eta_low is not None:
            run.eta_low = eta_low
        if eta_high is not None:
            run.eta_high = eta_high
        if current_item is not None:
            run.current_item = current_item
        if total_items is not None:
            run.total_items = total_items
        if completed_items is not None:
            run.completed_items = completed_items
        if failed_items is not None:
            run.failed_items = failed_items
        if in_flight_items is not None:
            run.in_flight_items = in_flight_items
        if summary:
            run.summary = summary
        if output is not None:
            run.output_json = output
        if error:
            run.error_message = error
        if start:
            run.started_at = _utcnow()
        if finish:
            run.finished_at = _utcnow()
        db.commit()
    finally:
        db.close()


def _calc_eta(task_id: str, agent_name: str, total: int, done: int) -> int:
    """根据当前平均耗时估算剩余秒数。"""
    if total <= 0 or done <= 0:
        return 0
    db = SessionLocal()
    try:
        run = (
            db.query(AgentRun)
            .filter(AgentRun.task_id == task_id, AgentRun.agent_name == agent_name)
            .first()
        )
        if run is None or run.started_at is None:
            return 0
        elapsed = (_utcnow() - run.started_at).total_seconds()
        per = elapsed / done
        remain = total - done
        return max(0, int(per * remain))
    finally:
        db.close()


def _calc_eta_range(durations: list[float], total: int, done: int, concurrency: int) -> tuple[int, int]:
    """按已完成样本和并发波次返回 P50 到 P90 的剩余时间区间。"""
    remaining = max(0, total - done)
    if remaining == 0:
        return 0, 0
    if not durations:
        return 0, 0
    import math
    from statistics import median

    s = sorted(durations)
    p50 = median(s)
    p90 = s[min(len(s) - 1, int(len(s) * 0.9))]
    waves = math.ceil(remaining / max(1, concurrency))
    low = int(waves * p50)
    high = int(waves * max(p50, p90))
    return low, high


# --------- 工作流节点 ---------


def _is_aborted(task_id: str, agent_name: str) -> bool:
    """检查用户是否已经中止当前智能体节点。"""
    db = SessionLocal()
    try:
        run = (
            db.query(AgentRun)
            .filter(AgentRun.task_id == task_id, AgentRun.agent_name == agent_name)
            .first()
        )
        return bool(run and run.status == "failed" and run.error_message == "用户中止")
    finally:
        db.close()


class _AbortedByUser(AbortFlow):
    """用户中止工作流；由 bounded_map 取消剩余任务。"""


def _check_aborted_or_raise(task_id: str, agent_name: str) -> None:
    if _is_aborted(task_id, agent_name):
        raise _AbortedByUser()


def node_parse_resume(state: JobScoutState) -> JobScoutState:
    task_id = state["task_id"]
    _update_run(task_id, "Resume Agent", status="running", progress=10, start=True)
    db = SessionLocal()
    try:
        resume = db.get(Resume, state["resume_id"])
        if resume is None:
            raise ValueError(f"简历 {state['resume_id']} 不存在")
        state["resume_text"] = resume.raw_text
        _update_run(task_id, "Resume Agent", progress=50, current_item="Resume Agent 正在解析画像…")
        # 已有画像则复用，否则调用快速档模型解析。
        if resume.profile_json:
            profile = ResumeProfile.model_validate(resume.profile_json)
        else:
            profile = resume_agent.run(resume.raw_text, model_role="fast")
            resume.profile_json = profile.model_dump()
            resume.content_hash = resume_agent.compute_hash(resume.raw_text)
            resume.parsed_at = _utcnow()
            resume.profile_version = (resume.profile_version or 0) + 1
            db.commit()
        state["resume_profile"] = profile.model_dump()
        _update_run(
            task_id,
            "Resume Agent",
            status="success",
            progress=100,
            summary=f"技能 {len(profile.skills)} 项，项目 {len(profile.projects)} 个",
            output=profile.model_dump(),
            eta_seconds=0,
            current_item="",
            finish=True,
        )
    except Exception as e:  # noqa: BLE001
        state.setdefault("errors", []).append(f"parse_resume: {e}")
        _update_run(task_id, "Resume Agent", status="failed", progress=100, error=str(e), finish=True)
    finally:
        db.close()
    return state


def node_abort(state: JobScoutState) -> JobScoutState:
    """简历解析失败时结束未执行节点，避免任务永久停在运行中。"""
    task_id = state["task_id"]
    reason = " | ".join(state.get("errors", [])[:3]) or "简历解析失败"
    for name in ("Job Agent", "Match Agent", "Report Agent"):
        _update_run(
            task_id,
            name,
            status="failed",
            progress=100,
            error=f"前置步骤失败，已中止：{reason}",
            finish=True,
        )
    return state


def _parse_single_job(work_item: dict) -> JobProfile:
    """解析单个预加载岗位；worker 不访问数据库。"""
    if work_item.get("cached_profile"):
        return work_item["cached_profile"]
    return job_agent.run(
        work_item["jd_text"],
        work_item["hints"],
        source=work_item["source"],
        model_role="fast",
    )


def _preload_job_work_items(job_ids: list[int]) -> list[dict]:
    """一次性预加载岗位数据，避免并发 worker 共享数据库会话。"""
    if not job_ids:
        return []
    db = SessionLocal()
    try:
        work_items = []
        for jid in job_ids:
            job = db.get(Job, jid)
            if job is None:
                continue
            existing = (
                db.query(JobAnalysis).filter(JobAnalysis.job_id == jid).first()
            )
            cached = (
                JobProfile.model_validate(existing.analysis_json)
                if existing and existing.analysis_json
                else None
            )
            work_items.append({
                "jid": jid,
                "hints": {
                    "company_name": job.company_name,
                    "job_title": job.job_title,
                    "city": job.city,
                    "salary": job.salary,
                },
                "jd_text": job.jd_text,
                "source": job.source,
                "cached_profile": cached,
            })
        return work_items
    finally:
        db.close()


def node_parse_jobs(state: JobScoutState) -> JobScoutState:
    task_id = state["task_id"]
    _update_run(
        task_id,
        "Job Agent",
        status="running",
        progress=0,
        start=True,
        current_item="准备开始解析岗位…",
        summary="准备开始解析岗位",
    )
    job_ids = state["job_ids"]
    total = len(job_ids)
    parsed: list[dict] = []
    settings = get_settings()
    done = 0

    # 主线程预加载 DB 数据，worker 线程只做 LLM，不碰 Session。
    work_items = _preload_job_work_items(job_ids)

    def _on_result(wi, profile, err):
        nonlocal done
        jid = wi["jid"]
        done += 1
        progress = int(done / total * 100) if total else 100
        label = f"正在解析岗位 {jid}（{done}/{total}）"
        if _is_aborted(task_id, "Job Agent"):
            raise _AbortedByUser()
        if err is not None or profile is None:
            state.setdefault("errors", []).append(f"parse_jobs job {jid}: {err}")
            _update_run(
                task_id,
                "Job Agent",
                progress=progress,
                summary=f"已解析 {done}/{total}（岗位 {jid} 失败：{err}）",
                current_item=label + " - 失败",
            )
            return
        # 主线程负责回填岗位字段并持久化结构化分析。
        db = SessionLocal()
        try:
            job = db.get(Job, jid)
            if job is not None:
                job.company_name = job.company_name or profile.company_name
                job.job_title = job.job_title or profile.job_title
                job.city = job.city or profile.city
                job.salary = job.salary or profile.salary
                job.parse_status = "success"
                job.parse_error = ""
                existing = db.query(JobAnalysis).filter(JobAnalysis.job_id == jid).first()
                if existing is None:
                    db.add(
                        JobAnalysis(
                            job_id=jid,
                            job_type=profile.job_type,
                            required_skills=profile.required_skills,
                            preferred_skills=profile.preferred_skills,
                            responsibilities=profile.responsibilities,
                            requirements=profile.requirements,
                            risk_tags=profile.risk_tags,
                            analysis_json=profile.model_dump(),
                        )
                    )
                else:
                    existing.job_type = profile.job_type
                    existing.required_skills = profile.required_skills
                    existing.preferred_skills = profile.preferred_skills
                    existing.responsibilities = profile.responsibilities
                    existing.requirements = profile.requirements
                    existing.risk_tags = profile.risk_tags
                    existing.analysis_json = profile.model_dump()
                db.commit()
            parsed.append({
                "job_id": jid,
                "profile": profile.model_dump(),
                "analyze_mode": job.analyze_mode or "summary",
            })
        except Exception as e:  # noqa: BLE001
            state.setdefault("errors", []).append(f"parse_jobs commit job {jid}: {e}")
            db.rollback()
        finally:
            db.close()
        eta = _calc_eta(task_id, "Job Agent", total, done)
        _update_run(
            task_id,
            "Job Agent",
            progress=progress,
            summary=f"已解析 {done}/{total}",
            current_item=label,
            eta_seconds=eta,
        )

    bounded_map(
        work_items,
        _parse_single_job,
        max_concurrency=settings.job_agent_concurrency,
        on_result=_on_result,
    )

    state["jobs_parsed"] = parsed
    if not parsed:
        _update_run(
            task_id,
            "Job Agent",
            status="failed",
            progress=100,
            summary="鎵€鏈夊矖浣嶈В鏋愬潎澶辫触",
            error=" | ".join(state.get("errors", [])[-5:]),
            eta_seconds=0,
            current_item="",
            finish=True,
        )
    else:
        _update_run(
            task_id,
            "Job Agent",
            status="success",
            progress=100,
            summary=f"解析岗位 {len(parsed)} 个"
            + (
                f"（{len(job_ids) - len(parsed)} 个失败）"
                if len(parsed) < len(job_ids)
                else ""
            ),
            output={"count": len(parsed)},
            eta_seconds=0,
            current_item="",
            finish=True,
        )
    return state


def node_match_jobs(state: JobScoutState) -> JobScoutState:
    task_id = state["task_id"]
    _update_run(
        task_id,
        "Match Agent",
        status="running",
        progress=0,
        start=True,
        current_item="准备开始匹配评分…",
        summary="准备开始匹配评分",
        total_items=len(state.get("jobs_parsed", [])),
    )
    resume = ResumeProfile.model_validate(state["resume_profile"])
    jobs = state.get("jobs_parsed", [])
    total = len(jobs)
    settings = get_settings()
    resume_id = state["resume_id"]
    two_tier = bool(settings.match_two_tier)
    quick_seconds = 40
    deep_seconds = 120

    # 并发可视化 / 计时需要的线程安全容器。
    lock = threading.Lock()
    start_times: dict[int, float] = {}
    in_flight: set[int] = set()
    cached_jids: set[int] = set()
    # quick 与 deep 分档计数，避免深度阶段进度溢出。
    phase_done = {"quick": 0, "deep": 0}
    phase_failed = {"quick": 0, "deep": 0}
    phase_durations = {"quick": [], "deep": []}
    title_map = {it["job_id"]: (it["profile"] or {}).get("job_title", "") for it in jobs}
    # 保存每个岗位最终采用的匹配结果；深度结果可覆盖快速结果。
    final_map: dict[int, dict] = {}
    job_mode_map: dict[int, str] = {
        it["job_id"]: it["analyze_mode"] or "summary"
        for it in jobs
        if "analyze_mode" in it
    }
    missing_mode_ids = [it["job_id"] for it in jobs if it["job_id"] not in job_mode_map]
    if missing_mode_ids:
        db_modes = SessionLocal()
        try:
            for row in db_modes.query(Job).filter(Job.id.in_(missing_mode_ids)).all():
                job_mode_map[row.id] = row.analyze_mode or "summary"
        except Exception:
            pass
        finally:
            db_modes.close()

    if two_tier:
        quick_targets = [it for it in jobs if job_mode_map.get(it["job_id"], "summary") != "full"]
        deep_targets = [it for it in jobs if job_mode_map.get(it["job_id"], "summary") == "full"]
    else:
        quick_targets = []
        deep_targets = list(jobs)

    total_cost_units = len(quick_targets) * quick_seconds + len(deep_targets) * deep_seconds

    # 预建 queued 记录，让前端能够实时展示排队状态。
    for it in jobs:
        upsert_item_run(
            task_id, "Match Agent", it["job_id"], title_map.get(it["job_id"], ""),
            tier="deep" if job_mode_map.get(it["job_id"], "summary") == "full" or not two_tier else "quick",
            status="queued",
        )

    def _emit_progress(tier: str, phase_total: int, label: str) -> None:
        """按实际完成成本计算进度，并根据耗时样本估算 ETA。"""
        done = phase_done[tier]
        completed_units = phase_done["quick"] * quick_seconds + phase_done["deep"] * deep_seconds
        progress = round(completed_units / max(1, total_cost_units) * 100) if total_cost_units else 100
        completed_items = phase_done["quick"] + phase_done["deep"]
        low, high = _calc_eta_range(
            phase_durations[tier], phase_total, done, settings.match_agent_concurrency
        )
        with lock:
            flight = [{"job_id": j, "job_title": title_map.get(j, "")} for j in in_flight]
            failed = phase_failed[tier]
        _update_run(
            task_id,
            "Match Agent",
            progress=min(100, progress),
            summary=f"已匹配 {done}/{phase_total}"
            + (f"（{failed} 个失败）" if failed else ""),
            current_item=label,
            eta_seconds=_calc_eta(
                task_id, "Match Agent", total, phase_done["quick"] + phase_done["deep"]
            ),
            eta_low=low,
            eta_high=high,
            total_items=total,
            completed_items=min(total, completed_items),
            failed_items=failed,
            in_flight_items=flight,
        )
        # 将当前并发任务标记为 running。
        for j in in_flight:
            upsert_item_run(
                task_id, "Match Agent", j, title_map.get(j, ""), tier=tier, status="running"
            )

    def _finish_item_run(jid, tier, st, now, status, error=""):
        dur_ms = int((now - st) * 1000) if st is not None else 0
        started = _utcnow() - timedelta(milliseconds=dur_ms) if st is not None else None
        upsert_item_run(
            task_id, "Match Agent", jid, title_map.get(jid, ""), tier=tier,
            status=status, error=error, started_at=started,
            finished_at=_utcnow(), duration_ms=dur_ms,
        )

    def _run_phase(tier: str, targets: list[dict]) -> None:
        """执行一档匹配：quick 处理基础岗位，deep 处理深度岗位。"""
        if not targets:
            return
        phase_total = len(targets)

        def _worker(item):
            jid = item["job_id"]
            with lock:
                start_times[jid] = time.monotonic()
                in_flight.add(jid)
            def _research_event(phase: str, metadata: dict) -> None:
                upsert_item_run(
                    task_id,
                    "Match Agent",
                    jid,
                    title_map.get(jid, ""),
                    tier=tier,
                    status="running",
                    phase=phase,
                    metadata=metadata,
                )

            return match_core.run_single_match(
                resume.model_dump(),
                resume_id,
                jid,
                tier=tier,
                research_callback=_research_event,
            )

        def _on_result(item, oc, _err):
            jid = item["job_id"]
            now = time.monotonic()
            cache_hit = bool(oc.cache_hit) if oc else False
            with lock:
                st = start_times.pop(jid, None)
                in_flight.discard(jid)
                if st is not None and not cache_hit:
                    phase_durations[tier].append(now - st)
            if _is_aborted(task_id, "Match Agent"):
                raise _AbortedByUser()
            label = f"正在匹配岗位 {jid}（{phase_done[tier] + 1}/{phase_total}）"
            if oc is None or oc.match is None:
                with lock:
                    phase_failed[tier] += 1
                # bounded_map 可能只返回异常；统一整理为可持久化错误。
                error = (oc.error if oc else None) or (_err or "未知错误")
                error = str(error)
                try:
                    fallback_status = "partial" if tier == "deep" and two_tier else "failed"
                    match_core.persist_match_row(
                        task_id, resume_id, jid, None,
                        (oc.key if oc else ""),
                        cache_hit,
                        match_mode=tier,
                        # 仅已有快速结果的深度任务失败时标记为 partial。
                        status=fallback_status,
                        error=error,
                    )
                except Exception as e:  # noqa: BLE001
                    state.setdefault("errors", []).append(f"match_jobs persist fail job {jid}: {e}")
                _finish_item_run(jid, tier, st, now, "failed", error=error)
                with lock:
                    phase_done[tier] += 1
                _emit_progress(tier, phase_total, label + " - 失败")
                return
            # 直接使用 persist_match_row 的返回值（行 id），不再临时开 Session 查询。
            # 这样能避免批量运行时累计未关闭的数据库连接。
            result_id = None
            try:
                result_id = match_core.persist_match_row(
                    task_id, resume_id, jid, oc.match, oc.key, oc.cache_hit,
                    match_mode=tier, status="success",
                )
            except Exception as e:  # noqa: BLE001
                state.setdefault("errors", []).append(f"match_jobs persist job {jid}: {e}")
            if cache_hit:
                with lock:
                    cached_jids.add(jid)
            _finish_item_run(jid, tier, st, now, "done")
            final_map[jid] = {
                "job_id": jid,
                "result_id": result_id,
                "match": oc.match.model_dump(),
            }
            with lock:
                phase_done[tier] += 1
            _emit_progress(tier, phase_total, label)

        bounded_map(
            targets,
            _worker,
            max_concurrency=settings.match_agent_concurrency,
            on_result=_on_result,
        )

    deep_count = len(deep_targets)
    if quick_targets:
        _run_phase("quick", quick_targets)
    if deep_targets:
        _run_phase("deep", deep_targets)

    # 汇总每个岗位的最终匹配结果并按分数降序排列。
    results = sorted(final_map.values(), key=lambda x: x["match"]["score"], reverse=True)
    state["match_results"] = results
    top = results[0]["match"]["score"] if results else 0

    if not results:
        _update_run(
            task_id,
            "Match Agent",
            status="failed",
            progress=100,
            summary="所有岗位匹配均失败",
            eta_seconds=0,
            eta_low=0,
            eta_high=0,
            total_items=total,
            completed_items=0,
            failed_items=phase_failed["quick"] + phase_failed["deep"],
            in_flight_items=[],
            current_item="",
            finish=True,
        )
    else:
        _update_run(
            task_id,
            "Match Agent",
            status="success",
            progress=100,
            summary=f"完成 {len(results)} 个岗位评分，最高 {top} 分"
            + (f"（{phase_failed['quick'] + phase_failed['deep']} 个失败）" if (phase_failed["quick"] + phase_failed["deep"]) else "")
            + (f"（{len(cached_jids)} 个命中缓存）" if cached_jids else "")
            + (f"（{deep_count} 个岗位做深度匹配）" if deep_count else ""),
            output={"count": len(results)},
            eta_seconds=0,
            eta_low=0,
            eta_high=0,
            total_items=total,
            completed_items=total,
            failed_items=phase_failed["quick"] + phase_failed["deep"],
            in_flight_items=[],
            current_item="",
            finish=True,
        )
    return state


def node_generate_report(state: JobScoutState) -> JobScoutState:
    """按配置生成基础报告；深度报告由结果页按需触发。"""
    task_id = state["task_id"]
    _update_run(
        task_id,
        "Report Agent",
        status="running",
        progress=0,
        start=True,
        current_item="准备生成报告…",
        summary="鍑嗗鐢熸垚鎶ュ憡",
    )
    settings = get_settings()
    policy = (settings.report_auto_policy or "top_k").lower()
    resume = ResumeProfile.model_validate(state["resume_profile"])
    parsed_map = {it["job_id"]: it["profile"] for it in state.get("jobs_parsed", [])}
    match_results = state.get("match_results", [])

    if policy == "none":
        _update_run(
            task_id,
            "Report Agent",
            status="success",
            progress=100,
            summary="已跳过自动报告（策略=none），可手动生成",
            eta_seconds=0,
            current_item="",
            finish=True,
        )
        return state

    if policy == "all":
        targets = match_results
    else:  # top_k
        targets = match_results[: max(1, settings.report_auto_top_k)]

    if not targets:
        _update_run(
            task_id,
            "Report Agent",
            status="success",
            progress=100,
            summary="鏃犲尮閰嶇粨鏋滐紝璺宠繃鎶ュ憡",
            eta_seconds=0,
            current_item="",
            finish=True,
        )
        return state

    items: list[dict] = []
    done = 0
    n = len(targets)
    for mr in targets:
        jid = mr["job_id"]
        job = JobProfile.model_validate(_parsed_map_safe(parsed_map, jid))
        match = MatchResultModel.model_validate(mr["match"])
        # 基础报告使用确定性模板，不调用 LLM。
        report = report_agent.build_standard_job_report(job, match)
        db = SessionLocal()
        try:
            row = db.get(MatchResult, mr["result_id"])
            if row and row.detail_json is not None:
                detail = dict(row.detail_json)
                detail["report"] = report
                row.detail_json = detail
                db.commit()
            items.append({"job": job, "match": match, "report": report})
        finally:
            db.close()
        done += 1
        _update_run(
            task_id,
            "Report Agent",
            progress=int(done / n * 100) if n else 100,
            summary=f"已生成基础报告 {done}/{n}",
            current_item=f"报告 {done}/{n}",
        )

    # 汇总并持久化基础版 Markdown 报告。
    markdown = report_agent.build_standard_markdown(resume, items)
    db = SessionLocal()
    try:
        report_row = Report(
            resume_id=state["resume_id"],
            task_id=task_id,
            title=f"岗位分析报告（Top {len(items)} 基础版） - {resume.name or '候选人'}",
            summary=f"共 {len(items)} 个岗位（基础报告，未调用 LLM）",
            markdown_content=markdown,
        )
        db.add(report_row)
        db.commit()
        # 仅保留最近的历史报告。
        try:
            from routers.reports import _prune_history_reports
            _prune_history_reports()
        except Exception:  # noqa: BLE001
            pass
        state["final_report"] = markdown
        state["report_id"] = report_row.id
        _update_run(
            task_id,
            "Report Agent",
            status="success",
            progress=100,
            summary=f"生成 {len(items)} 个基础报告（Top {settings.report_auto_top_k}）",
            output={"report_id": report_row.id, "mode": "standard"},
            eta_seconds=0,
            current_item="",
            finish=True,
        )
    finally:
        db.close()
    return state


def _parsed_map_safe(parsed_map: dict, jid) -> dict:
    """安全读取岗位画像；缺失时返回空字典。"""
    return parsed_map.get(jid, {}) or {}


def route_after_parse_resume(state: JobScoutState) -> str:
    """简历画像存在时继续解析岗位，否则中止。"""
    return "parse_jobs" if state.get("resume_profile") else "abort"


def _build_graph():
    g = StateGraph(JobScoutState)
    g.add_node("parse_resume", node_parse_resume)
    g.add_node("parse_jobs", node_parse_jobs)
    g.add_node("match_jobs", node_match_jobs)
    g.add_node("generate_report", node_generate_report)
    g.add_node("abort", node_abort)
    g.set_entry_point("parse_resume")
    g.add_conditional_edges(
        "parse_resume",
        route_after_parse_resume,
        {"parse_jobs": "parse_jobs", "abort": "abort"},
    )
    g.add_edge("abort", END)
    g.add_edge("parse_jobs", "match_jobs")
    g.add_edge("match_jobs", "generate_report")
    g.add_edge("generate_report", END)
    return g.compile()


_GRAPH = _build_graph()


def create_task(resume_id: int, job_ids: list[int]) -> str:
    task_id = uuid.uuid4().hex[:12]
    _init_runs(task_id)
    db = SessionLocal()
    try:
        match_run = (
            db.query(AgentRun)
            .filter(AgentRun.task_id == task_id, AgentRun.agent_name == "Match Agent")
            .first()
        )
        if match_run is not None:
            match_run.input_json = {
                "network_policy": {
                    "quick": "disabled",
                    "deep": "forced_with_model_fallback",
                }
            }
            db.commit()
    finally:
        db.close()
    return task_id


def _fail_unfinished_runs(task_id: str, error: str) -> None:
    """异常时结束未完成节点，避免任务永久显示运行中。"""
    db = SessionLocal()
    try:
        runs = (
            db.query(AgentRun)
            .filter(
                AgentRun.task_id == task_id,
                AgentRun.status.in_(["pending", "running"]),
            )
            .all()
        )
        now = _utcnow()
        for run in runs:
            run.status = "failed"
            run.progress = 100
            run.error_message = f"工作流异常：{error}"
            if run.started_at is None:
                run.started_at = now
            run.finished_at = now
        db.commit()
    finally:
        db.close()


def run_workflow(
    task_id: str,
    resume_id: int,
    job_ids: list[int],
) -> None:
    """同步执行完整工作流，供后台线程调用。"""
    state: JobScoutState = {
        "task_id": task_id,
        "resume_id": resume_id,
        "job_ids": job_ids,
        "errors": [],
    }
    try:
        _GRAPH.invoke(state)
    except _AbortedByUser:
        # 用户中止后，统一结束尚未执行的节点。
        for name in ("Resume Agent", "Job Agent", "Match Agent", "Report Agent"):
            _update_run(
                task_id,
                name,
                status="failed",
                progress=100,
                error="鐢ㄦ埛涓",
                finish=True,
            )
    except Exception as exc:  # noqa: BLE001
        _fail_unfinished_runs(task_id, str(exc))
