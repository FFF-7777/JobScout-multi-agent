"""Report Agent：生成单个岗位的投递建议与面试准备，并可汇总为 Markdown 报告。"""
from __future__ import annotations

import hashlib
import json

import prompts
from schemas.job import JobProfile
from schemas.match import MatchResultModel
from schemas.report import JobReport
from schemas.resume import ResumeProfile
from services import llm_service

# 报告模板 / 融合逻辑版本号，作为报告缓存键的一部分：
# 改了报告模板或评分口径时递增，使旧缓存失效、触发重新生成。
REPORT_PROMPT_VERSION = "1"


def build_report_cache_key(
    resume_profile: dict,
    job_profile: dict,
    match: dict,
    model: str,
    mode: str,
    prompt_version: str = REPORT_PROMPT_VERSION,
) -> str:
    """报告缓存键：相同 简历画像 + 岗位画像 + 匹配结果 + 模型 + 报告模式 + Prompt版本 时复用。

    命中后（标准报告 / 深度报告）直接复用已生成内容，跳过 LLM 调用。返回 32 位十六进制串。
    """
    payload = {
        "resume": resume_profile,
        "job": job_profile,
        "match": match,
        "model": model,
        "mode": mode,
        "prompt_version": prompt_version,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def run(
    resume: ResumeProfile,
    job: JobProfile,
    match: MatchResultModel,
    *,
    resume_text: str | None = None,
    model_role: str = "report",
) -> JobReport:
    """生成单岗位投递建议 + 面试准备。

    resume_text：可选简历原文（截断 8000 char），让模型能引用具体项目细节。
    """
    resume_block = json.dumps(resume.model_dump(), ensure_ascii=False)
    if resume_text:
        resume_block += (
            "\n\n--- 简历原文（用于引用项目细节 / 自我评价）---\n"
            + resume_text[:8000]
        )
    data = llm_service.chat_json(
        prompts.REPORT_SYSTEM,
        prompts.REPORT_USER.format(
            resume_profile=resume_block,
            job_profile=json.dumps(job.model_dump(), ensure_ascii=False),
            match_result=json.dumps(match.model_dump(), ensure_ascii=False),
        ),
        model_role=model_role,
    )
    return JobReport.model_validate(data)


def build_standard_job_report(job: JobProfile, match: MatchResultModel) -> dict:
    """基础报告：纯代码模板，立即生成，不调用 LLM。

    只基于 Match Agent 已有的结构化结果组织，不重新计算分数、不编造事实。
    用于自动 Top-K 报告与「生成基础报告」按需场景；深度 AI 报告请调 run()。
    """
    interview_focus = [
        f"准备说明你在「{p}」方面的实际项目证据（做了什么、用了什么技术、结果如何）"
        for p in (match.matched_points or [])[:3]
    ]
    if not interview_focus:
        interview_focus = [
            "回顾简历中与岗位要求最贴近的项目，准备讲清你做了什么、用了什么技术、结果如何"
        ]
    return {
        "conclusion": match.recommendation,
        "priority": f"匹配度 {match.score} 分（{match.level} 级）",
        "matched_points": match.matched_points or [],
        "missing_points": match.missing_points or [],
        "risk_notes": match.risk_notes or [],
        "interview_focus": interview_focus,
        "mode": "standard",
    }


def build_standard_markdown(
    resume: ResumeProfile,
    items: list[dict],
) -> str:
    """把多个岗位的基础报告（代码模板）汇总为 Markdown。

    items: [{"job": JobProfile, "match": MatchResultModel, "report": dict}]
    """
    lines: list[str] = []
    lines.append(f"# JobScout 岗位分析报告（基础版）— {resume.name or '候选人'}\n")
    lines.append("> 本报告由代码模板即时生成（未调用 LLM）。如需面试题 / BOSS 话术等深度内容，可对岗位点「生成深度报告」。\n")
    lines.append("## 岗位推荐排序\n")
    lines.append("| 排名 | 公司 | 岗位 | 城市 | 薪资 | 匹配度 | 等级 | 建议 |")
    lines.append("| ---: | --- | --- | --- | --- | ---: | :--: | --- |")
    for i, it in enumerate(items, 1):
        j: JobProfile = it["job"]
        m: MatchResultModel = it["match"]
        lines.append(
            f"| {i} | {j.company_name} | {j.job_title} | {j.city} | {j.salary} "
            f"| {m.score} | {m.level} | {m.recommendation} |"
        )
    lines.append("")

    lines.append("## 岗位详细分析\n")
    for i, it in enumerate(items, 1):
        j: JobProfile = it["job"]
        m: MatchResultModel = it["match"]
        r: dict = it["report"]
        lines.append(f"### {i}. {j.company_name} — {j.job_title}（{m.level} / {m.score} 分）\n")
        lines.append(f"**推荐结论**：{r.get('conclusion', '')}　|　**优先级**：{r.get('priority', '')}\n")
        if r.get("matched_points"):
            lines.append("**匹配点：**")
            lines.extend(f"- {p}" for p in r["matched_points"])
        if r.get("missing_points"):
            lines.append("\n**缺口分析：**")
            lines.extend(f"- {p}" for p in r["missing_points"])
        if r.get("risk_notes"):
            lines.append("\n**风险提醒：**")
            lines.extend(f"- {p}" for p in r["risk_notes"])
        if r.get("interview_focus"):
            lines.append("\n**面试准备重点：**")
            lines.extend(f"- {p}" for p in r["interview_focus"])
        lines.append("\n---\n")

    return "\n".join(lines)


def build_markdown(
    resume: ResumeProfile,
    items: list[dict],
) -> str:
    """把多个岗位的匹配 + 报告汇总为 Markdown 全量报告。

    items: [{"job": JobProfile, "match": MatchResultModel, "report": JobReport}]
    """
    lines: list[str] = []
    lines.append(f"# JobScout 岗位分析报告 — {resume.name or '候选人'}\n")
    lines.append("## 候选人画像\n")
    lines.append(f"- **目标岗位**：{'、'.join(resume.target_roles) or '（未填写）'}")
    lines.append(f"- **技能**：{'、'.join(resume.skills) or '（未填写）'}")
    if resume.strengths:
        lines.append(f"- **优势**：{'、'.join(resume.strengths)}")
    if resume.weaknesses:
        lines.append(f"- **短板**：{'、'.join(resume.weaknesses)}")
    lines.append("")

    # 按匹配度排序
    ordered = sorted(items, key=lambda x: x["match"].score, reverse=True)

    lines.append("## 岗位推荐排序\n")
    lines.append("| 排名 | 公司 | 岗位 | 城市 | 薪资 | 匹配度 | 等级 | 建议 |")
    lines.append("| ---: | --- | --- | --- | --- | ---: | :--: | --- |")
    for i, it in enumerate(ordered, 1):
        j: JobProfile = it["job"]
        m: MatchResultModel = it["match"]
        lines.append(
            f"| {i} | {j.company_name} | {j.job_title} | {j.city} | {j.salary} "
            f"| {m.score} | {m.level} | {m.recommendation} |"
        )
    lines.append("")

    lines.append("## 岗位详细分析\n")
    for i, it in enumerate(ordered, 1):
        j: JobProfile = it["job"]
        m: MatchResultModel = it["match"]
        r: JobReport = it["report"]
        lines.append(f"### {i}. {j.company_name} — {j.job_title}（{m.level} / {m.score} 分）\n")
        lines.append(f"**推荐结论**：{r.conclusion}　|　**优先级**：{r.priority}\n")
        if m.matched_points:
            lines.append("**匹配点：**")
            lines.extend(f"- {p}" for p in m.matched_points)
        if m.missing_points:
            lines.append("\n**缺口分析：**")
            lines.extend(f"- {p}" for p in m.missing_points)
        if r.risks or m.risk_notes:
            lines.append("\n**风险提醒：**")
            lines.extend(f"- {p}" for p in (r.risks + m.risk_notes))
        if r.interview_questions:
            lines.append("\n**面试可能问题：**")
            lines.extend(f"- {p}" for p in r.interview_questions)
        if r.project_talking_points:
            lines.append("\n**项目讲解重点：**")
            lines.extend(f"- {p}" for p in r.project_talking_points)
        if r.boss_greeting:
            lines.append(f"\n**BOSS 打招呼话术：**\n> {r.boss_greeting}")
        if r.hr_message:
            lines.append(f"\n**HR 私信：**\n> {r.hr_message}")
        if r.improvement_tips:
            lines.append("\n**短板补习建议：**")
            lines.extend(f"- {p}" for p in r.improvement_tips)
        lines.append("\n---\n")

    return "\n".join(lines)


def build_hybrid_report_markdown(
    resume: ResumeProfile,
    items: list[dict],
) -> str:
    """深度报告生成完成后重新聚合的 Markdown。

    items: [{"job": JobProfile, "match": MatchResultModel, "report": dict}]
    report dict 可以是 basic（mode=standard，含 interview_focus）
    或 deep（mode=deep，含 interview_questions / boss_greeting / hr_message 等）。
    混合展示：深度内容优先，基础内容兜底。
    """
    lines: list[str] = []
    has_deep = any((it.get("report") or {}).get("mode") == "deep" for it in items)
    lines.append(
        f"# JobScout 岗位分析报告{'（含深度分析）' if has_deep else ''} — {resume.name or '候选人'}\n"
    )
    lines.append("## 候选人画像\n")
    lines.append(f"- **目标岗位**：{'、'.join(resume.target_roles) or '（未填写）'}")
    lines.append(f"- **技能**：{'、'.join(resume.skills) or '（未填写）'}")
    if resume.strengths:
        lines.append(f"- **优势**：{'、'.join(resume.strengths)}")
    if resume.weaknesses:
        lines.append(f"- **短板**：{'、'.join(resume.weaknesses)}")
    lines.append("")

    ordered = sorted(items, key=lambda x: x["match"].score, reverse=True)
    lines.append("## 岗位推荐排序\n")
    lines.append("| 排名 | 公司 | 岗位 | 城市 | 薪资 | 匹配度 | 等级 | 建议 |")
    lines.append("| ---: | --- | --- | --- | --- | ---: | :--: | --- |")
    for i, it in enumerate(ordered, 1):
        j: JobProfile = it["job"]
        m: MatchResultModel = it["match"]
        lines.append(
            f"| {i} | {j.company_name} | {j.job_title} | {j.city} | {j.salary} "
            f"| {m.score} | {m.level} | {m.recommendation} |"
        )
    lines.append("")

    lines.append("## 岗位详细分析\n")
    for i, it in enumerate(ordered, 1):
        j: JobProfile = it["job"]
        m: MatchResultModel = it["match"]
        r: dict = it.get("report") or {}
        is_deep = r.get("mode") == "deep"
        label = "深度分析" if is_deep else "基础分析"
        lines.append(
            f"### {i}. {j.company_name} — {j.job_title}（{m.level} / {m.score} 分）"
            f" <sub><sup>[{label}]</sup></sub>\n"
        )
        lines.append(f"**推荐结论**：{r.get('conclusion', m.recommendation)}\n")
        if m.matched_points:
            lines.append("**匹配点：**")
            lines.extend(f"- {p}" for p in m.matched_points)
        if m.missing_points:
            lines.append("\n**缺口分析：**")
            lines.extend(f"- {p}" for p in m.missing_points)
        risk = (r.get("risks") or []) + (m.risk_notes or [])
        if risk:
            lines.append("\n**风险提醒：**")
            lines.extend(f"- {p}" for p in risk)
        if is_deep:
            iq = r.get("interview_questions") or []
            if iq:
                lines.append("\n**面试可能问题：**")
                lines.extend(f"- {p}" for p in iq)
            pt = r.get("project_talking_points") or []
            if pt:
                lines.append("\n**项目讲解重点：**")
                lines.extend(f"- {p}" for p in pt)
            if r.get("boss_greeting"):
                lines.append(f"\n**BOSS 打招呼话术：**\n> {r['boss_greeting']}")
            if r.get("hr_message"):
                lines.append(f"\n**HR 私信：**\n> {r['hr_message']}")
            imp = r.get("improvement_tips") or []
            if imp:
                lines.append("\n**短板补习建议：**")
                lines.extend(f"- {p}" for p in imp)
        else:
            ifv = r.get("interview_focus") or []
            if ifv:
                lines.append("\n**面试准备重点：**")
                lines.extend(f"- {p}" for p in ifv)
        lines.append("\n---\n")

    return "\n".join(lines)
