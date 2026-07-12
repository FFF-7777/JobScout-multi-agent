"""LangGraph 工作流：串联 4 个 Agent，并把每一步执行状态落库。

节点顺序：parse_resume -> parse_jobs -> match_jobs -> generate_report
每个节点执行前后写 agent_runs 表，供前端轮询做过程可视化。

耗时优化：parse_jobs / match_jobs / generate_report 三个节点内部对 N 个岗位
做 LLM 调用并发，worker 内只跑 chat_json，主线程按完成顺序落库 + 推进 progress。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TypedDict

from langgraph.graph import END, StateGraph

from config import get_settings
from database import SessionLocal
from models import AgentRun, Job, JobAnalysis, MatchResult, Report, Resume
from schemas.job import JobProfile
from schemas.match import MatchResultModel
from schemas.report import JobReport
from schemas.resume import ResumeProfile
from services import job_agent, match_agent, report_agent, resume_agent
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
    current_item: str | None = None,
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
        if current_item is not None:
            run.current_item = current_item
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
        # 已有画像则复用，否则调用 Agent
        if resume.profile_json:
            profile = ResumeProfile.model_validate(resume.profile_json)
        else:
            profile = resume_agent.run(resume.raw_text)
            resume.profile_json = profile.model_dump()
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
        return job_agent.run(job.jd_text, hints)
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
        max_concurrency=settings.llm_concurrency,
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


def _match_single(jid_and_profile):
    """worker：在子线程跑 LLM（match_agent.run）。返回 (jid, match_or_None, err)。"""
    jid, profile_dict = jid_and_profile
    try:
        job = JobProfile.model_validate(profile_dict)
        # 简历对象在子线程只读（不动 DB），由 worker 入口传入
        # 真正的 resume 序列化由 _on_match_result 闭包提供
        return _match_single  # type: ignore[return-value]
    except Exception as e:  # noqa: BLE001
        return jid, None, str(e)


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
    )
    resume = ResumeProfile.model_validate(state["resume_profile"])
    jobs = state.get("jobs_parsed", [])
    total = len(jobs)
    results: list[dict] = []
    settings = get_settings()
    done = 0

    def _worker(item):
        # 实际跑 LLM；resume 通过闭包传进去
        job = JobProfile.model_validate(item["profile"])
        return match_agent.run(resume, job)

    def _on_result(item, match, err):
        nonlocal done
        done += 1
        jid = item["job_id"]
        progress = int(done / total * 100) if total else 100
        label = f"正在匹配岗位 {jid}（{done}/{total}）"
        if _is_aborted(task_id, "Match Agent"):
            raise _AbortedByUser()
        if err is not None or match is None:
            state.setdefault("errors", []).append(f"match_jobs job {jid}: {err}")
            _update_run(
                task_id,
                "Match Agent",
                progress=progress,
                summary=f"已匹配 {done}/{total}（岗位 {jid} 失败：{err}）",
                current_item=label + " — 失败",
            )
            return
        db = SessionLocal()
        try:
            row = MatchResult(
                resume_id=state["resume_id"],
                job_id=jid,
                task_id=task_id,
                score=match.score,
                level=match.level,
                matched_points=match.matched_points,
                missing_points=match.missing_points,
                recommendation=match.recommendation,
                risk_notes=match.risk_notes,
                detail_json=match.model_dump(),
            )
            db.add(row)
            db.commit()
            results.append({"job_id": jid, "result_id": row.id, "match": match.model_dump()})
        except Exception as e:  # noqa: BLE001
            state.setdefault("errors", []).append(f"match_jobs commit job {jid}: {e}")
            db.rollback()
        finally:
            db.close()
        eta = _calc_eta(task_id, "Match Agent", total, done)
        _update_run(
            task_id,
            "Match Agent",
            progress=progress,
            summary=f"已匹配 {done}/{total}",
            current_item=label,
            eta_seconds=eta,
        )

    bounded_map(
        jobs,
        _worker,
        max_concurrency=settings.llm_concurrency,
        on_result=_on_result,
    )

    results.sort(key=lambda x: x["match"]["score"], reverse=True)
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
            current_item="",
            finish=True,
        )
    else:
        _update_run(
            task_id,
            "Match Agent",
            status="success",
            progress=100,
            summary=f"完成 {len(results)} 个岗位评分，最高 {top} 分",
            output={"count": len(results)},
            eta_seconds=0,
            current_item="",
            finish=True,
        )
    return state


def node_generate_report(state: JobScoutState) -> JobScoutState:
    task_id = state["task_id"]
    _update_run(
        task_id,
        "Report Agent",
        status="running",
        progress=0,
        start=True,
        current_item="准备开始生成报告…",
        summary="准备开始生成报告",
    )
    resume = ResumeProfile.model_validate(state["resume_profile"])
    parsed_map = {it["job_id"]: it["profile"] for it in state.get("jobs_parsed", [])}
    match_results = state.get("match_results", [])
    total = len(match_results)
    items: list[dict] = []
    settings = get_settings()
    done = 0

    def _worker(mr):
        job = JobProfile.model_validate(_parsed_map_safe(parsed_map, mr["job_id"]))
        match = MatchResultModel.model_validate(mr["match"])
        return report_agent.run(resume, job, match)

    def _on_result(mr_payload, report, err):
        nonlocal done
        done += 1
        jid = mr_payload["job_id"]
        result_id = mr_payload["result_id"]
        progress = int(done / total * 100) if total else 100
        label = f"正在生成报告 {jid}（{done}/{total}）"
        if _is_aborted(task_id, "Report Agent"):
            raise _AbortedByUser()
        if err is not None or report is None:
            state.setdefault("errors", []).append(f"generate_report job {jid}: {err}")
            _update_run(
                task_id,
                "Report Agent",
                progress=progress,
                summary=f"已生成 {done}/{total}（岗位 {jid} 失败：{err}）",
                current_item=label + " — 失败",
            )
            return
        db = SessionLocal()
        try:
            job = JobProfile.model_validate(_parsed_map_safe(parsed_map, jid))
            match = MatchResultModel.model_validate(mr_payload["match"])
            row = db.get(MatchResult, result_id)
            if row and row.detail_json is not None:
                detail = dict(row.detail_json)
                detail["report"] = report.model_dump()
                row.detail_json = detail
                db.commit()
            items.append({"job": job, "match": match, "report": report})
        except Exception as e:  # noqa: BLE001
            state.setdefault("errors", []).append(f"generate_report commit job {jid}: {e}")
            db.rollback()
        finally:
            db.close()
        eta = _calc_eta(task_id, "Report Agent", total, done)
        _update_run(
            task_id,
            "Report Agent",
            progress=progress,
            summary=f"已生成 {done}/{total}",
            current_item=label,
            eta_seconds=eta,
        )

    bounded_map(
        match_results,
        _worker,
        max_concurrency=settings.llm_concurrency,
        on_result=_on_result,
    )

    if not items:
        _update_run(
            task_id,
            "Report Agent",
            status="failed",
            progress=100,
            summary="所有岗位报告生成均失败",
            eta_seconds=0,
            current_item="",
            finish=True,
        )
        return state

    markdown = report_agent.build_markdown(resume, items)
    db = SessionLocal()
    try:
        report_row = Report(
            resume_id=state["resume_id"],
            task_id=task_id,
            title=f"岗位分析报告 - {resume.name or '候选人'}",
            summary=f"共分析 {len(items)} 个岗位",
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
            summary=f"生成报告，覆盖 {len(items)} 个岗位",
            output={"report_id": report_row.id},
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
