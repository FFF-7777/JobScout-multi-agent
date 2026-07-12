"""LangGraph 工作流：串联 4 个 Agent，并把每一步执行状态落库。

节点顺序：parse_resume -> parse_jobs -> match_jobs -> generate_report
每个节点执行前后写 agent_runs 表，供前端轮询做过程可视化。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TypedDict

from langgraph.graph import END, StateGraph

from database import SessionLocal
from models import AgentRun, Job, JobAnalysis, MatchResult, Report, Resume
from schemas.job import JobProfile
from schemas.match import MatchResultModel
from schemas.report import JobReport
from schemas.resume import ResumeProfile
from services import job_agent, match_agent, report_agent, resume_agent

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
    progress: int = 0,
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
        run.progress = progress
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


# --------- 各节点 ---------

def node_parse_resume(state: JobScoutState) -> JobScoutState:
    task_id = state["task_id"]
    _update_run(task_id, "Resume Agent", status="running", progress=10, start=True)
    db = SessionLocal()
    try:
        resume = db.get(Resume, state["resume_id"])
        if resume is None:
            raise ValueError(f"简历 {state['resume_id']} 不存在")
        state["resume_text"] = resume.raw_text
        _update_run(task_id, "Resume Agent", progress=50)
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


def node_parse_jobs(state: JobScoutState) -> JobScoutState:
    task_id = state["task_id"]
    _update_run(task_id, "Job Agent", status="running", progress=0, start=True)
    db = SessionLocal()
    parsed: list[dict] = []
    try:
        job_ids = state["job_ids"]
        total = len(job_ids)
        for idx, jid in enumerate(job_ids, 1):
            progress = int(idx / total * 100) if total else 100
            try:
                job = db.get(Job, jid)
                if job is None:
                    _update_run(task_id, "Job Agent", progress=progress)
                    continue
                existing = (
                    db.query(JobAnalysis).filter(JobAnalysis.job_id == jid).first()
                )
                if existing and existing.analysis_json:
                    profile = JobProfile.model_validate(existing.analysis_json)
                else:
                    hints = {
                        "company_name": job.company_name,
                        "job_title": job.job_title,
                        "city": job.city,
                        "salary": job.salary,
                    }
                    profile = job_agent.run(job.jd_text, hints)
                    # 回填岗位主表空字段
                    job.company_name = job.company_name or profile.company_name
                    job.job_title = job.job_title or profile.job_title
                    job.city = job.city or profile.city
                    job.salary = job.salary or profile.salary
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
                    db.commit()
                parsed.append({"job_id": jid, "profile": profile.model_dump()})
            except Exception as e:  # noqa: BLE001
                # 单个岗位解析失败：记录错误并继续，不拖垮整段
                state.setdefault("errors", []).append(f"parse_jobs job {jid}: {e}")
                db.rollback()
            _update_run(task_id, "Job Agent", progress=progress)
        state["jobs_parsed"] = parsed
        if not parsed:
            _update_run(
                task_id,
                "Job Agent",
                status="failed",
                progress=100,
                summary="所有岗位解析均失败",
                error="；".join(state.get("errors", [])[-5:]),
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
                finish=True,
            )
    finally:
        db.close()
    return state


def node_match_jobs(state: JobScoutState) -> JobScoutState:
    task_id = state["task_id"]
    _update_run(task_id, "Match Agent", status="running", progress=0, start=True)
    db = SessionLocal()
    results: list[dict] = []
    try:
        resume = ResumeProfile.model_validate(state["resume_profile"])
        jobs = state.get("jobs_parsed", [])
        total = len(jobs)
        for idx, item in enumerate(jobs, 1):
            progress = int(idx / total * 100) if total else 100
            try:
                jid = item["job_id"]
                job = JobProfile.model_validate(item["profile"])
                match = match_agent.run(resume, job)
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
                results.append(
                    {"job_id": jid, "result_id": row.id, "match": match.model_dump()}
                )
            except Exception as e:  # noqa: BLE001
                state.setdefault("errors", []).append(
                    f"match_jobs job {item.get('job_id')}: {e}"
                )
                db.rollback()
            _update_run(task_id, "Match Agent", progress=progress)
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
                finish=True,
            )
    finally:
        db.close()
    return state


def node_generate_report(state: JobScoutState) -> JobScoutState:
    task_id = state["task_id"]
    _update_run(task_id, "Report Agent", status="running", progress=0, start=True)
    db = SessionLocal()
    items: list[dict] = []
    try:
        resume = ResumeProfile.model_validate(state["resume_profile"])
        parsed_map = {it["job_id"]: it["profile"] for it in state.get("jobs_parsed", [])}
        match_results = state.get("match_results", [])
        total = len(match_results)
        for idx, mr in enumerate(match_results, 1):
            progress = int(idx / total * 100) if total else 100
            try:
                jid = mr["job_id"]
                job = JobProfile.model_validate(_parsed_map_safe(parsed_map, jid))
                match = MatchResultModel.model_validate(mr["match"])
                report = report_agent.run(resume, job, match)
                # 回写单岗位报告到 match_results.detail_json
                row = db.get(MatchResult, mr["result_id"])
                if row and row.detail_json is not None:
                    detail = dict(row.detail_json)
                    detail["report"] = report.model_dump()
                    row.detail_json = detail
                    db.commit()
                items.append({"job": job, "match": match, "report": report})
            except Exception as e:  # noqa: BLE001
                state.setdefault("errors", []).append(
                    f"generate_report job {mr.get('job_id')}: {e}"
                )
                db.rollback()
            _update_run(task_id, "Report Agent", progress=progress)

        if not items:
            _update_run(
                task_id,
                "Report Agent",
                status="failed",
                progress=100,
                summary="所有岗位报告生成均失败",
                finish=True,
            )
            return state

        markdown = report_agent.build_markdown(resume, items)
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
    _GRAPH.invoke(state)
