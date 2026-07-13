"""LangGraph 宸ヤ綔娴侊細涓茶仈 4 涓?Agent锛屽苟鎶婃瘡涓€姝ユ墽琛岀姸鎬佽惤搴撱€?
鑺傜偣椤哄簭锛歱arse_resume -> parse_jobs -> match_jobs -> generate_report
姣忎釜鑺傜偣鎵ц鍓嶅悗鍐?agent_runs 琛紝渚涘墠绔疆璇㈠仛杩囩▼鍙鍖栥€?
鑰楁椂浼樺寲锛歱arse_jobs / match_jobs / generate_report 涓変釜鑺傜偣鍐呴儴瀵?N 涓矖浣?鍋?LLM 璋冪敤骞跺彂锛寃orker 鍐呭彧璺?chat_json锛屼富绾跨▼鎸夊畬鎴愰『搴忚惤搴?+ 鎺ㄨ繘 progress銆?"""
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
    """tz 瀹夊叏鐨?naive UTC 鏃堕棿锛堥伩鍏?datetime.utcnow() 鐨?DeprecationWarning锛夈€?"""
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
    """ETA = (宸茬敤 / 宸插畬鎴? * 鍓╀綑銆俤one=0 鏃惰繑鍥?0銆?"""
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
    """鍩轰簬宸插畬鎴愬崟宀椾綅鑰楁椂鏍锋湰锛岀粰鍑哄墿浣欐椂闂寸殑 P50~P90 鑼冨洿锛堢锛夈€?
    鍓嶅嚑涓牱鏈笉瓒虫椂杩斿洖 (0, 0)锛屽墠绔嵁姝ゆ樉绀恒€屼及绠椾腑鈥︺€嶃€?    鍗曞矖浣嶈€楁椂娉㈠姩澶э紙杈撳叆闀垮害 / 闄愭祦閲嶈瘯 / 缃戠粶锛夛紝鏁呭睍绀哄尯闂磋€岄潪绮剧‘绉掓暟銆?    """
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


# --------- 鍚勮妭鐐?---------


def _is_aborted(task_id: str, agent_name: str) -> bool:
    """鐢ㄦ埛璋冧簡 abort_task锛氬綋鍓嶈妭鐐逛細琚爣璁颁负 failed銆俹n_result / 鑺傜偣鍏ュ彛妫€鏌ヤ竴娆″嵆鍙€?"""
    db = SessionLocal()
    try:
        run = (
            db.query(AgentRun)
            .filter(AgentRun.task_id == task_id, AgentRun.agent_name == agent_name)
            .first()
        )
        return bool(run and run.status == "failed" and run.error_message == "鐢ㄦ埛涓")
    finally:
        db.close()


class _AbortedByUser(AbortFlow):
    """鐢ㄦ埛鍦?workflow 璺戞湡闂寸偣浜嗐€屼腑鏂€嶃€傜户鎵?AbortFlow 璁?bounded_map 鍙栨秷鍓╀綑 future銆?"""


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
        # 宸叉湁鐢诲儚鍒欏鐢紝鍚﹀垯璋冪敤 Agent锛坒ast 妗ｏ級
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
    """绠€鍘嗚В鏋愬け璐ユ椂鏀跺熬锛氭妸鏈墽琛岀殑涓嬫父鑺傜偣鏍囪涓哄け璐ワ紝閬垮厤浠诲姟鍗″湪 running銆?"""
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
    """worker 鍑芥暟锛氱函 LLM 璋冪敤锛屼笉纰?DB銆?    
    work_item 鐢?_preload_job_work_items() 鍦ㄤ富绾跨▼棰勫姞杞斤紝
    鍖呭惈 hints / jd_text / source / cached_profile锛堣嫢宸叉湁鍒嗘瀽缂撳瓨锛夈€?    """
    if work_item.get("cached_profile"):
        return work_item["cached_profile"]
    return job_agent.run(
        work_item["jd_text"],
        work_item["hints"],
        source=work_item["source"],
        model_role="fast",
    )


def _preload_job_work_items(job_ids: list[int]) -> list[dict]:
    """涓荤嚎绋嬩竴娆℃€ф煡璇㈡墍鏈夊矖浣嶇殑 DB 鏁版嵁锛屾墦鍖呮垚 worker 鍙洿鎺ヤ娇鐢ㄧ殑 work_item銆?    
    姣忎釜 work_item 鍖呭惈锛?    - jid: 宀椾綅 ID
    - hints: {company_name, job_title, city, salary}
    - jd_text: 鍘熷 JD 鏂囨湰
    - source: 鏉ユ簮锛坢anual / boss / ocr 绛夛級
    - cached_profile: JobProfile 瀹炰緥锛堣嫢宸叉湁鍒嗘瀽缂撳瓨锛夛紝None 琛ㄧず闇€瑕?LLM
    """
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

    # 涓荤嚎绋嬮鍔犺浇 DB 鏁版嵁锛寃orker 绾跨▼鍙仛 LLM锛堜笉纰?Session锛?    work_items = _preload_job_work_items(job_ids)

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
        # 涓荤嚎绋?commit锛氬洖濉?jobs 琛ㄧ┖瀛楁 + 钀?job_analysis
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
    quick_seconds = 20
    deep_seconds = 90

    # 骞跺彂鍙鍖?/ 璁℃椂鎵€闇€鐨勭嚎绋嬪畨鍏ㄥ鍣?    lock = threading.Lock()
    start_times: dict[int, float] = {}
    in_flight: set[int] = set()
    cached_jids: set[int] = set()
    # P0#2锛歲uick / deep 涓ら樁娈靛垎鍒鏁颁笌璁℃椂锛岄伩鍏?deep 闃舵杩涘害婧㈠嚭 100%
    phase_done = {"quick": 0, "deep": 0}
    phase_failed = {"quick": 0, "deep": 0}
    phase_durations = {"quick": [], "deep": []}
    title_map = {it["job_id"]: (it["profile"] or {}).get("job_title", "") for it in jobs}
    # jid -> (MatchResultModel, result_row_id)锛氭渶缁堬紙鍙兘缁?deep 瑕嗙洊锛夌殑鍖归厤缁撴灉
    final_map: dict[int, dict] = {}
    job_mode_map: dict[int, str] = {}
    db_modes = SessionLocal()
    try:
        for row in db_modes.query(Job).filter(Job.id.in_([it["job_id"] for it in jobs])).all():
            job_mode_map[row.id] = row.analyze_mode or "summary"
    finally:
        db_modes.close()

    if two_tier:
        quick_targets = [it for it in jobs if job_mode_map.get(it["job_id"], "summary") != "full"]
        deep_targets = [it for it in jobs if job_mode_map.get(it["job_id"], "summary") == "full"]
    else:
        quick_targets = []
        deep_targets = list(jobs)

    total_cost_units = len(quick_targets) * quick_seconds + len(deep_targets) * deep_seconds

    # 棰勫缓 queued 鐘舵€佺殑鍗曟潯鎵ц璁板綍锛屼緵鍓嶇瀹炴椂鐪嬪埌鎺掗槦
    for it in jobs:
        upsert_item_run(
            task_id, "Match Agent", it["job_id"], title_map.get(it["job_id"], ""),
            tier="deep" if job_mode_map.get(it["job_id"], "summary") == "full" or not two_tier else "quick",
            status="queued",
        )

    def _emit_progress(tier: str, phase_total: int, label: str) -> None:
        """鎸夐樁娈靛垎鍒绠楄繘搴︿笌 ETA锛圥0#2 淇娣卞害闃舵婧㈠嚭 100%锛夈€?
        - quick 闃舵锛氳繘搴?= done/phase_total * 70锛沜ompleted_items = 鏈樁娈靛凡瀹屾垚鏁?        - deep 闃舵锛氳繘搴?= 70 + done/phase_total * 30锛沜ompleted_items = total
          锛堝叏閲?quick 缁撴灉宸茬粡鍙煡鐪嬶紝涓嶅簲鎶?deep 璋冪敤娆℃暟鍐嶆绱姞鍒板矖浣嶆暟锛?           鍚﹀垯 deep 绗竴涓换鍔″氨浼氬嚭鐜?70 + 34/5*30 = 274%锛?        """
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
        # 鎶婂湪閫?item 鏍囪涓?running锛堝崟鏉℃墽琛岃褰曪級
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
        """瀵?targets 璺戜竴妗ｅ尮閰嶏紱tier=quick 鍏ㄩ噺棰勬帓锛宼ier=deep 浠?Top-K / 鐢ㄦ埛鎸囧畾 full銆?"""
        if not targets:
            return
        phase_total = len(targets)

        def _worker(item):
            jid = item["job_id"]
            with lock:
                start_times[jid] = time.monotonic()
                in_flight.add(jid)
            return match_core.run_single_match(resume.model_dump(), resume_id, jid, tier=tier)

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
                # worker 鎶涘紓甯告椂 oc 涓?None锛坆ounded_map 浠呬紶 error锛夛紱鍚﹀垯鍙?oc.error
                error = (oc.error if oc else None) or (_err or "鏈煡閿欒")
                error = str(error)
                try:
                    fallback_status = "partial" if tier == "deep" and two_tier else "failed"
                    match_core.persist_match_row(
                        task_id, resume_id, jid, None,
                        (oc.key if oc else ""),
                        cache_hit,
                        match_mode=tier,
                        # 鍙湁鈥滃厛 quick 鍚?deep鈥濈殑宀椾綅锛宒eep 澶辫触鎵嶇畻 partial锛涘惁鍒欏氨鏄?failed
                        status=fallback_status,
                        error=error,
                    )
                except Exception as e:  # noqa: BLE001
                    state.setdefault("errors", []).append(f"match_jobs persist fail job {jid}: {e}")
                _finish_item_run(jid, tier, st, now, "failed", error=error)
                with lock:
                    phase_done[tier] += 1
                _emit_progress(tier, phase_total, label + " 鈥?澶辫触")
                return
            # P1#8锛氱洿鎺ョ敤 persist_match_row 鐨勮繑鍥炲€硷紙琛?id锛夛紝涓嶅啀寮€涓存椂 Session 鏌ヨ锛?            # 閬垮厤鎵归噺杩愯鏃剁疮绉湭鍏抽棴鐨勬暟鎹簱杩炴帴銆?            result_id = None
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

    # 姹囨€绘渶缁堝尮閰嶇粨鏋滐紙鎸夋渶缁堝垎鏁伴檷搴忥級锛屼緵涓嬫父鎶ュ憡鑺傜偣浣跨敤
    results = sorted(final_map.values(), key=lambda x: x["match"]["score"], reverse=True)
    state["match_results"] = results
    top = results[0]["match"]["score"] if results else 0

    if not results:
        _update_run(
            task_id,
            "Match Agent",
            status="failed",
            progress=100,
            summary="鎵€鏈夊矖浣嶅尮閰嶅潎澶辫触",
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
    """鑷姩鎶ュ憡鐢熸垚锛堜笉闃诲鐢ㄦ埛鐪嬪尮閰嶇粨鏋滐級銆?
    绛栫暐鐢?REPORT_AUTO_POLICY 鎺у埗锛?    - none锛氫笉鑷姩鐢熸垚锛屽叏閮ㄦ寜闇€锛堢敤鎴峰彲鍦ㄧ粨鏋滈〉鐐广€岀敓鎴愭姤鍛娿€嶏級
    - all 锛氫负鎵€鏈夊矖浣嶇敓鎴?    - top_k锛堥粯璁わ級锛氫粎瀵瑰尮閰嶅害鏈€楂樼殑 N 涓矖浣嶇敓鎴?
    鑷姩鐢熸垚鐨勬姤鍛婇粯璁ゆ槸銆屽熀纭€鎶ュ憡銆嶏紙绾唬鐮佹ā鏉匡紝绔嬪嵆鐢熸垚锛岄浂 LLM 璋冪敤锛夛紱
    娣卞害 AI 鎶ュ憡锛堝惈闈㈣瘯棰?/ BOSS 璇濇湳绛夛級閫氳繃 POST /api/reports/generate-batch 鎸夐渶瑙﹀彂銆?    """
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
        # 鍩虹鎶ュ憡锛氱函浠ｇ爜妯℃澘锛岀珛鍗崇敓鎴愶紝涓嶈皟鐢?LLM
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
            summary=f"宸茬敓鎴愬熀纭€鎶ュ憡 {done}/{n}",
            current_item=f"鎶ュ憡 {done}/{n}",
        )

    # 姹囨€?Markdown锛堝熀纭€鎶ュ憡锛夎惤搴擄紝渚涖€屾姤鍛婂鍑恒€嶉〉鏌ョ湅 / Excel 瀵煎嚭
    markdown = report_agent.build_standard_markdown(resume, items)
    db = SessionLocal()
    try:
        report_row = Report(
            resume_id=state["resume_id"],
            task_id=task_id,
            title=f"宀椾綅鍒嗘瀽鎶ュ憡锛圱op {len(items)} 鍩虹鐗堬級 - {resume.name or '鍊欓€変汉'}",
            summary=f"共 {len(items)} 个岗位（基础报告，未调用 LLM）",
            markdown_content=markdown,
        )
        db.add(report_row)
        db.commit()
        # 淇濈暀鏈€杩?100 浠藉巻鍙叉姤鍛婏紝瓒呭嚭鏈€鏃х殑鑷姩鍒犻櫎
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
    """鍙栧矖浣嶇敾鍍忥紝缂哄け鏃剁粰绌?dict锛岄伩鍏嶅崟宀椾綅鏃犵敾鍍忓鑷存暣娈靛け璐ャ€?"""
    return parsed_map.get(jid, {}) or {}


def route_after_parse_resume(state: JobScoutState) -> str:
    """绠€鍘嗙敾鍍忔湭鐢熸垚鍒欎腑姝紝鍚﹀垯杩涘叆宀椾綅瑙ｆ瀽銆?"""
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


def _fail_unfinished_runs(task_id: str, error: str) -> None:
    """宸ヤ綔娴佸彂鐢熸湭棰勬湡寮傚父鏃剁粨鏉熸湭瀹屾垚鑺傜偣锛岄伩鍏嶄换鍔℃案涔呮樉绀轰负杩愯涓€?"""
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
            run.error_message = f"宸ヤ綔娴佸紓甯革細{error}"
            if run.started_at is None:
                run.started_at = now
            run.finished_at = now
        db.commit()
    finally:
        db.close()


def run_workflow(task_id: str, resume_id: int, job_ids: list[int]) -> None:
    """鍚屾鎵ц鏁翠釜宸ヤ綔娴侊紙渚涘悗鍙扮嚎绋嬭皟鐢級銆?"""
    state: JobScoutState = {
        "task_id": task_id,
        "resume_id": resume_id,
        "job_ids": job_ids,
        "errors": [],
    }
    try:
        _GRAPH.invoke(state)
    except _AbortedByUser:
        # 鐢ㄦ埛涓锛氭妸"杩樻病璺戝埌"鐨勪笅娓歌妭鐐圭粺涓€鏍囪涓?failed/宸蹭腑姝?        for name in ("Resume Agent", "Job Agent", "Match Agent", "Report Agent"):
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
