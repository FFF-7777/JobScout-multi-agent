"""LangGraph 工作流：串联 4 个 Agent，并把每一步执行状态落库。

节点顺序：parse_resume -> parse_jobs -> match_jobs -> generate_report
每个节点执行前后写 agent_runs 表，供前端轮询做过程可视化。

耗时优化：parse_jobs / match_jobs / generate_report 三个节点内部对 N 个岗位
做 LLM 调用并发，worker 内只跑 chat_json，主线程按完成顺序落库 + 推进 progress。
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta
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


# --------- agent_runs 落库工具 ---------


def _init_runs(task_id: str) -> None:
    db = SessionLocal()
    try:
        for order, (node, name) in enumerate(AGENT_STEPS, 1):
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
            run.started_at = datetime.utcnow()
        if finish:
            run.finished_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def _calc_eta(task_id: str, agent_name: str, total: int, done: int) -> int:
    """ETA = (已用 / 已完成) * 剩余。done=0 时返回 0。"""
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
        elapsed = (datetime.utcnow() - run.started_at).total_seconds()
        per = elapsed / done
        remain = total - done
        return max(0, int(per * remain))
    finally:
        db.close()


def _calc_eta_range(durations: list[float], total: int, done: int, concurrency: int) -> tuple[int, int]:
    """基于已完成单岗位耗时样本，给出剩余时间的 P50~P90 范围（秒）。

    前几个样本不足时返回 (0, 0)，前端据此显示「估算中…」。
    单岗位耗时波动大（输入长度 / 限流重试 / 网络），故展示区间而非精确秒数。
    """
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


# --------- 各节点 ---------


def _is_aborted(task_id: str, agent_name: str) -> bool:
    """用户调了 abort_task：当前节点会被标记为 failed。on_result / 节点入口检查一次即可。"""
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
    """用户在 workflow 跑期间点了「中断」。继承 AbortFlow 让 bounded_map 取消剩余 future。"""


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
        # 已有画像则复用，否则调用 Agent（fast 档）
        if resume.profile_json:
            profile = ResumeProfile.model_validate(resume.profile_json)
        else:
            profile = resume_agent.run(resume.raw_text, model_role="fast")
            resume.profile_json = profile.model_dump()
            resume.content_hash = resume_agent.compute_hash(resume.raw_text)
            resume.parsed_at = datetime.utcnow()
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
    """简历解析失败时收尾：把未执行的下游节点标记为失败，避免任务卡在 running。"""
    task_id = state["task_id"]
    reason = "；".join(state.get("errors", [])[:3]) or "简历解析失败"
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


def _parse_single_job(jid: int):
    """worker 函数：在子线程跑 LLM；不碰 DB。返回 profile（JobProfile）。失败抛异常。"""
    db = SessionLocal()
    try:
        job = db.get(Job, jid)
        if job is None:
            raise ValueError(f"岗位 {jid} 不存在")
        existing = (
            db.query(JobAnalysis).filter(JobAnalysis.job_id == jid).first()
        )
        if existing and existing.analysis_json:
            return JobProfile.model_validate(existing.analysis_json)
        hints = {
            "company_name": job.company_name,
            "job_title": job.job_title,
            "city": job.city,
            "salary": job.salary,
        }
        return job_agent.run(job.jd_text, hints, source=job.source, model_role="fast")
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

    def _on_result(jid, profile, err):
        nonlocal done
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
                current_item=label + " — 失败",
            )
            return
        # 主线程 commit：回填 jobs 表空字段 + 落 job_analysis
        db = SessionLocal()
        try:
            job = db.get(Job, jid)
            if job is not None:
                job.company_name = job.company_name or profile.company_name
                job.job_title = job.job_title or profile.job_title
                job.city = job.city or profile.city
                job.salary = job.salary or profile.salary
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
            parsed.append({"job_id": jid, "profile": profile.model_dump()})
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
        job_ids,
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
            summary="所有岗位解析均失败",
            error="；".join(state.get("errors", [])[-5:]),
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
    top_k = max(1, settings.match_quick_top_k)

    # 并发可视化 / 计时所需的线程安全容器
    lock = threading.Lock()
    start_times: dict[int, float] = {}
    in_flight: set[int] = set()
    cached_jids: set[int] = set()
    counts = {"failed": 0, "done": 0}
    durations: list[float] = []
    title_map = {it["job_id"]: (it["profile"] or {}).get("job_title", "") for it in jobs}
    # jid -> (MatchResultModel, result_row_id)：最终（可能经 deep 覆盖）的匹配结果
    final_map: dict[int, dict] = {}

    # 预建 queued 状态的单条执行记录，供前端实时看到排队
    for it in jobs:
        upsert_item_run(
            task_id, "Match Agent", it["job_id"], title_map.get(it["job_id"], ""),
            tier="quick", status="queued",
        )

    def _emit_progress(phase: str, done_n: int, label: str):
        if phase == "quick":
            progress = int(done_n / total * 70) if total else 70
        else:
            b_total = max(1, min(top_k, total))
            progress = 70 + int(done_n / b_total * 30)
        low, high = _calc_eta_range(durations, total, counts["done"], settings.match_agent_concurrency)
        with lock:
            flight = [{"job_id": j, "job_title": title_map.get(j, "")} for j in in_flight]
            failed = counts["failed"]
        _update_run(
            task_id,
            "Match Agent",
            progress=progress,
            summary=f"已匹配 {counts['done']}/{total}"
            + (f"（{failed} 个失败）" if failed else ""),
            current_item=label,
            eta_seconds=_calc_eta(task_id, "Match Agent", total, counts["done"]),
            eta_low=low,
            eta_high=high,
            total_items=total,
            completed_items=counts["done"],
            failed_items=failed,
            in_flight_items=flight,
        )
        # 把在途 item 标记为 running（单条执行记录）
        for j in in_flight:
            upsert_item_run(
                task_id, "Match Agent", j, title_map.get(j, ""), tier="quick", status="running"
            )

    def _finish_item_run(jid, tier, st, now, status, error=""):
        dur_ms = int((now - st) * 1000) if st is not None else 0
        started = datetime.utcnow() - timedelta(milliseconds=dur_ms) if st is not None else None
        upsert_item_run(
            task_id, "Match Agent", jid, title_map.get(jid, ""), tier=tier,
            status=status, error=error, started_at=started,
            finished_at=datetime.utcnow(), duration_ms=dur_ms,
        )

    def _run_phase(tier: str, targets: list[dict], phase: str) -> None:
        """对 targets 跑一档匹配；tier=quick 全量预排，tier=deep 仅 Top-K 深度。"""
        if not targets:
            return

        def _worker(item):
            jid = item["job_id"]
            with lock:
                start_times[jid] = time.monotonic()
                in_flight.add(jid)
            return match_core.run_single_match(resume.model_dump(), resume_id, jid, tier=tier)

        def _on_result(item, oc, _err):
            jid = item["job_id"]
            now = time.monotonic()
            with lock:
                st = start_times.pop(jid, None)
                in_flight.discard(jid)
                if st is not None:
                    durations.append(now - st)
            if _is_aborted(task_id, "Match Agent"):
                raise _AbortedByUser()
            label = f"正在匹配岗位 {jid}（{counts['done'] + 1}/{total}）"
            if oc is None or oc.match is None:
                # 失败也落库，便于前端展示错误并支持单条重试
                with lock:
                    counts["failed"] += 1
                # worker 抛异常时 oc 为 None（bounded_map 仅传 error）；否则取 oc.error
                error = (oc.error if oc else None) or (_err or "未知错误")
                error = str(error)
                try:
                    match_core.persist_match_row(
                        task_id, resume_id, jid, None,
                        (oc.key if oc else ""),
                        (oc.cache_hit if oc else False),
                        match_mode=tier, status="failed", error=error,
                    )
                except Exception as e:  # noqa: BLE001
                    state.setdefault("errors", []).append(f"match_jobs persist fail job {jid}: {e}")
                _finish_item_run(jid, tier, st, now, "failed", error=error)
                counts["done"] += 1
                _emit_progress(phase, counts["done"], label + " — 失败")
                return
            try:
                match_core.persist_match_row(
                    task_id, resume_id, jid, oc.match, oc.key, oc.cache_hit,
                    match_mode=tier, status="success",
                )
            except Exception as e:  # noqa: BLE001
                state.setdefault("errors", []).append(f"match_jobs persist job {jid}: {e}")
            if oc.cache_hit:
                with lock:
                    cached_jids.add(jid)
            _finish_item_run(jid, tier, st, now, "done")
            # 记录最终匹配（quick 先写，deep 后覆盖）
            try:
                row = (
                    SessionLocal()
                    .query(MatchResult)
                    .filter(MatchResult.task_id == task_id, MatchResult.job_id == jid)
                    .first()
                )
                rid = row.id if row else None
                final_map[jid] = {"job_id": jid, "result_id": rid, "match": oc.match.model_dump()}
            except Exception as e:  # noqa: BLE001
                state.setdefault("errors", []).append(f"match_jobs query job {jid}: {e}")
            counts["done"] += 1
            _emit_progress(phase, counts["done"], label)

        bounded_map(
            targets,
            _worker,
            max_concurrency=settings.match_agent_concurrency,
            on_result=_on_result,
        )

    # PHASE A：全量 quick（快速模型）出分排序
    _run_phase("quick", jobs, "quick")

    # 按 quick 分数选出 Top-K（仅成功项参与排序）
    ranked = sorted(
        (v for v in final_map.values() if v["match"].get("score") is not None),
        key=lambda x: x["match"]["score"],
        reverse=True,
    )
    if two_tier:
        # PHASE B：Top-K 用推理模型做深度匹配（覆盖 quick 结果）
        deep_targets = [{"job_id": v["job_id"]} for v in ranked[:top_k]]
        for t in deep_targets:
            upsert_item_run(
                task_id, "Match Agent", t["job_id"],
                title_map.get(t["job_id"], ""), tier="deep", status="queued",
            )
        _run_phase("deep", deep_targets, "deep")

    # 汇总最终匹配结果（按最终分数降序），供下游报告节点使用
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
            failed_items=counts["failed"],
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
            + (f"（{counts['failed']} 个失败）" if counts["failed"] else "")
            + (f"（{len(cached_jids)} 个命中缓存）" if cached_jids else "")
            + (f"（Top {top_k} 做深度匹配）" if two_tier else ""),
            output={"count": len(results)},
            eta_seconds=0,
            eta_low=0,
            eta_high=0,
            total_items=total,
            completed_items=total,
            failed_items=counts["failed"],
            in_flight_items=[],
            current_item="",
            finish=True,
        )
    return state


def node_generate_report(state: JobScoutState) -> JobScoutState:
    """自动报告生成（不阻塞用户看匹配结果）。

    策略由 REPORT_AUTO_POLICY 控制：
    - none：不自动生成，全部按需（用户可在结果页点「生成报告」）
    - all ：为所有岗位生成
    - top_k（默认）：仅对匹配度最高的 N 个岗位生成

    自动生成的报告默认是「基础报告」（纯代码模板，立即生成，零 LLM 调用）；
    深度 AI 报告（含面试题 / BOSS 话术等）通过 POST /api/reports/generate-batch 按需触发。
    """
    task_id = state["task_id"]
    _update_run(
        task_id,
        "Report Agent",
        status="running",
        progress=0,
        start=True,
        current_item="准备生成报告…",
        summary="准备生成报告",
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
            summary="无匹配结果，跳过报告",
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
        # 基础报告：纯代码模板，立即生成，不调用 LLM
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

    # 汇总 Markdown（基础报告）落库，供「报告导出」页查看 / Excel 导出
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
    """取岗位画像，缺失时给空 dict，避免单岗位无画像导致整段失败。"""
    return parsed_map.get(jid, {}) or {}


def route_after_parse_resume(state: JobScoutState) -> str:
    """简历画像未生成则中止，否则进入岗位解析。"""
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
    return task_id


def run_workflow(task_id: str, resume_id: int, job_ids: list[int]) -> None:
    """同步执行整个工作流（供后台线程调用）。"""
    state: JobScoutState = {
        "task_id": task_id,
        "resume_id": resume_id,
        "job_ids": job_ids,
        "errors": [],
    }
    try:
        _GRAPH.invoke(state)
    except _AbortedByUser:
        # 用户中止：把"还没跑到"的下游节点统一标记为 failed/已中止
        for name in ("Resume Agent", "Job Agent", "Match Agent", "Report Agent"):
            _update_run(
                task_id,
                name,
                status="failed",
                progress=100,
                error="用户中止",
                finish=True,
            )
