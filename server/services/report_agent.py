"""Report Agent：基于已有匹配证据组织投递建议与面试准备。"""
from __future__ import annotations

import hashlib
import json

import prompts
from schemas.job import JobProfile
from schemas.match import MatchResultModel, ResearchMetadata
from schemas.report import JobReport
from schemas.resume import ResumeProfile
from services import llm_service, research_router, web_research_service

REPORT_PROMPT_VERSION = "4"


def build_report_cache_key(
    resume_profile: dict,
    job_profile: dict,
    match: dict,
    model: str,
    mode: str,
    prompt_version: str = REPORT_PROMPT_VERSION,
) -> str:
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
    research_plan = research_router.build_research_plan(
        job,
        tier="deep",
        analyze_mode="full",
    )
    research = web_research_service.fetch_research_context(job, plan=research_plan)
    resume_block = json.dumps(resume.model_dump(), ensure_ascii=False)
    if resume_text:
        resume_block += "\n\n--- 简历原文（用于补充细节引用）---\n" + resume_text[:8000]
    research_block = json.dumps(
        {
            "status": research.status,
            "summary_items": research.summary_items,
            "source_notes": research.source_notes,
            "sources": research.sources,
            "fallback_note": (
                "联网调用失败，以下报告由模型基于简历、岗位和匹配结果继续生成。"
                if research.status != "success"
                else "联网研究成功，报告应优先使用可核验的外部语境。"
            ),
        },
        ensure_ascii=False,
    )
    user_prompt = prompts.REPORT_USER.format(
        resume_profile=resume_block,
        job_profile=json.dumps(job.model_dump(), ensure_ascii=False),
        match_result=json.dumps(match.model_dump(), ensure_ascii=False),
    )
    user_prompt += "\n\n--- 本次强制联网研究结果 ---\n" + research_block
    data = llm_service.chat_json(
        prompts.REPORT_SYSTEM,
        user_prompt,
        model_role=model_role,
    )
    report = JobReport.model_validate(data)
    report.research_metadata = ResearchMetadata(
        status=research.status,
        attempted=research.attempted,
        queries=research.queries,
        source_notes=research.source_notes,
        sources=research.sources,
        provider=research.provider,
        verifiable=research.verifiable,
        reason=research.reason,
        error=research.error,
    )
    return report


def _build_decision_basis(match: MatchResultModel) -> list[str]:
    basis: list[str] = []
    for item in match.top_strengths[:3]:
        basis.append(f"{item.job_relevance} → {item.resume_evidence} → 形成优势判断")
    for item in match.main_gaps[:2]:
        basis.append(f"{item.title} → {item.impact or '存在明显缺口'} → 影响投递优先级")
    return basis[:5]


def _build_interview_focus(match: MatchResultModel) -> list[str]:
    items = [
        f"围绕“{item.title}”准备 1-2 分钟项目案例，重点讲清背景、职责、技术决策和结果；可用证据：{item.resume_evidence or '当前简历未提供'}"
        for item in match.top_strengths[:3]
    ]
    if items:
        return items
    return ["回顾最贴近岗位要求的项目，准备说明你做了什么、为什么这样做、结果如何。"]


def build_standard_job_report(job: JobProfile, match: MatchResultModel) -> dict:
    strengths = [
        {
            "title": item.title,
            "evidence": item.resume_evidence,
            "relevance": item.job_relevance,
        }
        for item in match.top_strengths
    ]
    gaps = [item.model_dump() for item in match.main_gaps]
    decision_basis = _build_decision_basis(match)
    return {
        "mode": "standard",
        "conclusion": match.recommendation,
        "priority": f"匹配度 {match.score} 分（{match.level} 级）",
        "executive_summary": match.application_decision.summary or match.recommendation,
        "decision_basis": decision_basis,
        "screening": {
            "result": match.hr_screening.likely_result,
            "reason": match.hr_screening.main_reason,
            "career_score": match.career_alignment.score,
            "career_analysis": match.career_alignment.analysis,
            "confidence": match.confidence,
        },
        "dimensions": match.dimensions.model_dump(),
        "strengths": strengths,
        "gaps": gaps,
        "skill_evidence": [item.model_dump() for item in match.skill_evidence],
        "skill_evidence_summary": match.skill_evidence_summary.model_dump(),
        "action_plan": match.next_actions or [item["action"] for item in gaps if item.get("action")][:3],
        "matched_points": match.matched_points or [],
        "missing_points": match.missing_points or [],
        "risk_notes": match.risk_notes or [],
        "interview_focus": _build_interview_focus(match),
        "research_summary": match.research_summary,
    }


def build_standard_markdown(
    resume: ResumeProfile,
    items: list[dict],
) -> str:
    lines: list[str] = [
        "# 岗位投递决策报告／基础分析",
        f"**候选人：** {resume.name or '未命名候选人'}　　**分析岗位：** {len(items)} 个",
        "",
        "> 基础分析基于简历、岗位画像与匹配证据生成，回答“值不值得投、为什么、投递前要做什么”。",
        "",
        "## 决策总览",
        "",
        "| 排名 | 公司与岗位 | 地点 / 薪资 | 匹配度 | 决策 |",
        "| ---: | --- | --- | ---: | --- |",
    ]
    for index, item in enumerate(items, 1):
        job: JobProfile = item["job"]
        match: MatchResultModel = item["match"]
        lines.append(
            f"| {index} | **{job.company_name or '未知公司'}**<br>{job.job_title or '未知岗位'}"
            f" | {job.city or '未注明'} / {job.salary or '未注明'} | **{match.score}**（{match.level}） | {match.recommendation} |"
        )

    dimension_labels = {
        "tech_stack": "技术栈",
        "project_exp": "项目经验",
        "role_direction": "岗位方向",
        "qualification": "资格条件",
        "logistics": "地点与薪资",
    }
    for index, item in enumerate(items, 1):
        job: JobProfile = item["job"]
        match: MatchResultModel = item["match"]
        report: dict = item["report"]
        screening = report.get("screening") or {}
        lines.extend(
            [
                "",
                "---",
                "",
                f"## {index}. {job.company_name or '未知公司'}｜{job.job_title or '未知岗位'}",
                "",
                f"> **{report.get('conclusion') or match.recommendation}**　·　{report.get('priority', '')}",
                "",
                report.get("executive_summary") or match.recommendation,
                "",
                "### 决策依据",
                "",
            ]
        )
        for point in report.get("decision_basis") or []:
            lines.append(f"- {point}")

        lines.extend(
            [
                "",
                "| HR 初筛 | 职业方向 | 评估置信度 |",
                "| --- | ---: | ---: |",
                f"| {screening.get('reason') or '暂无额外说明'} | {screening.get('career_score', '—')} | {screening.get('confidence', '—')}% |",
                "",
                "| 评估维度 | 得分 | 说明 |",
                "| --- | ---: | --- |",
            ]
        )
        dimensions = report.get("dimensions") or {}
        for key, label in dimension_labels.items():
            score = dimensions.get(key, "—")
            note = "优势项" if isinstance(score, (int, float)) and score >= 75 else "需要补证据" if isinstance(score, (int, float)) and score < 60 else "基本匹配"
            lines.append(f"| {label} | {score} | {note} |")

        lines.extend(["", "### 核心优势", ""])
        strengths = report.get("strengths") or []
        if strengths:
            for i, strength in enumerate(strengths, 1):
                lines.append(f"**{i}. {strength.get('title', '匹配优势')}**")
                lines.append(f"- 简历证据：{strength.get('evidence') or '当前简历未提供明确证据'}")
                lines.append(f"- 岗位关联：{strength.get('relevance') or '需结合岗位要求进一步核实'}")
        else:
            lines.append("当前材料中没有形成明显竞争优势，建议先补强简历证据。")

        lines.extend(["", "### 关键缺口与影响", ""])
        gaps = report.get("gaps") or []
        if gaps:
            severity_map = {"fatal": "高风险", "major": "主要缺口", "minor": "次要缺口"}
            for i, gap in enumerate(gaps, 1):
                lines.append(f"**{i}. {gap.get('title', '能力缺口')}｜{severity_map.get(gap.get('severity'), '待核实')}**")
                lines.append(f"- 影响：{gap.get('impact') or '需进一步核实'}")
                lines.append(f"- 处理建议：{gap.get('action') or '补充相关证据并准备解释'}")
        else:
            lines.append("未识别到明确的主要缺口。")

        lines.extend(["", "### 投递前行动清单", ""])
        actions = report.get("action_plan") or []
        if actions:
            lines.extend(f"- [ ] {action}" for action in actions)
        else:
            lines.append("- [ ] 核对硬性条件，并为最相关项目准备可验证的结果说明。")

        research_summary = report.get("research_summary") or match.research_summary or []
        if research_summary:
            lines.extend(["", "### 深度研究补充", ""])
            lines.extend(f"- {item}" for item in research_summary)

        lines.extend(["", "### 面试准备重点", ""])
        lines.extend(f"{i}. {focus}" for i, focus in enumerate(report.get('interview_focus') or [], 1))

    return "\n".join(lines).strip() + "\n"


def build_markdown(
    resume: ResumeProfile,
    items: list[dict],
) -> str:
    lines: list[str] = [
        f"# JobScout 岗位分析报告 — {resume.name or '候选人'}",
        "",
        "## 候选人画像",
        "",
        f"- **目标岗位：** {'、'.join(resume.target_roles) or '（未填写）'}",
        f"- **技能：** {'、'.join(resume.skills) or '（未填写）'}",
    ]
    if resume.strengths:
        lines.append(f"- **优势：** {'、'.join(resume.strengths)}")
    if resume.weaknesses:
        lines.append(f"- **短板：** {'、'.join(resume.weaknesses)}")
    lines.append("")

    ordered = sorted(items, key=lambda x: x["match"].score, reverse=True)
    lines.extend(
        [
            "## 岗位推荐排序",
            "",
            "| 排名 | 公司 | 岗位 | 城市 | 薪资 | 匹配度 | 等级 | 建议 |",
            "| ---: | --- | --- | --- | --- | ---: | :--: | --- |",
        ]
    )
    for index, item in enumerate(ordered, 1):
        job: JobProfile = item["job"]
        match: MatchResultModel = item["match"]
        lines.append(
            f"| {index} | {job.company_name} | {job.job_title} | {job.city} | {job.salary} | {match.score} | {match.level} | {match.recommendation} |"
        )
    lines.append("")

    lines.append("## 岗位详细分析\n")
    for index, item in enumerate(ordered, 1):
        job: JobProfile = item["job"]
        match: MatchResultModel = item["match"]
        report: JobReport = item["report"]
        lines.append(f"### {index}. {job.company_name} — {job.job_title}（{match.level} / {match.score} 分）\n")
        lines.append(f"**推荐结论：** {report.conclusion}　|　**优先级：** {report.priority}\n")
        if match.matched_points:
            lines.append("**匹配点：**")
            lines.extend(f"- {point}" for point in match.matched_points)
        if match.missing_points:
            lines.append("\n**缺口分析：**")
            lines.extend(f"- {point}" for point in match.missing_points)
        if report.risks or match.risk_notes:
            lines.append("\n**风险提醒：**")
            lines.extend(f"- {point}" for point in (report.risks + match.risk_notes))
        if report.interview_questions:
            lines.append("\n**面试可能问题：**")
            lines.extend(f"- {point}" for point in report.interview_questions)
        if report.project_talking_points:
            lines.append("\n**项目讲解重点：**")
            lines.extend(f"- {point}" for point in report.project_talking_points)
        if report.boss_greeting:
            lines.append(f"\n**BOSS 打招呼话术：**\n> {report.boss_greeting}")
        if report.hr_message:
            lines.append(f"\n**HR 私信：**\n> {report.hr_message}")
        if report.improvement_tips:
            lines.append("\n**短板补强建议：**")
            lines.extend(f"- {point}" for point in report.improvement_tips)
        lines.append("\n---\n")

    return "\n".join(lines)


def build_hybrid_report_markdown(
    resume: ResumeProfile,
    items: list[dict],
) -> str:
    lines: list[str] = []
    has_deep = any((item.get("report") or {}).get("mode") == "deep" for item in items)
    lines.append(f"# 岗位投递决策报告／{'深度分析' if has_deep else '基础分析'}\n")
    lines.append(f"**候选人：** {resume.name or '未命名候选人'}　　**分析岗位：** {len(items)} 个\n")
    lines.append("> 本报告围绕投递决策、简历表达和面试胜率展开；所有结论均基于当前简历与岗位信息。\n")
    lines.append("## 候选人定位\n")
    lines.append(f"- **目标岗位：** {'、'.join(resume.target_roles) or '（未填写）'}")
    lines.append(f"- **技能：** {'、'.join(resume.skills) or '（未填写）'}")
    if resume.strengths:
        lines.append(f"- **优势：** {'、'.join(resume.strengths)}")
    if resume.weaknesses:
        lines.append(f"- **短板：** {'、'.join(resume.weaknesses)}")
    lines.append("")

    ordered = sorted(items, key=lambda x: x["match"].score, reverse=True)
    lines.append("## 岗位推荐排序\n")
    lines.append("| 排名 | 公司 | 岗位 | 城市 | 薪资 | 匹配度 | 等级 | 建议 |")
    lines.append("| ---: | --- | --- | --- | --- | ---: | :--: | --- |")
    for index, item in enumerate(ordered, 1):
        job: JobProfile = item["job"]
        match: MatchResultModel = item["match"]
        lines.append(
            f"| {index} | {job.company_name} | {job.job_title} | {job.city} | {job.salary} | {match.score} | {match.level} | {match.recommendation} |"
        )
    lines.append("")

    lines.append("## 岗位详细分析\n")
    for index, item in enumerate(ordered, 1):
        job: JobProfile = item["job"]
        match: MatchResultModel = item["match"]
        report: dict = item.get("report") or {}
        is_deep = report.get("mode") == "deep"
        label = "深度分析" if is_deep else "基础分析"
        lines.append(f"### {index}. {job.company_name} — {job.job_title}（{match.level} / {match.score} 分） <sub><sup>[{label}]</sup></sub>\n")
        lines.append(f"> **{report.get('conclusion', match.recommendation)}**　·　{report.get('priority', f'{match.score} 分 / {match.level} 级')}\n")
        if report.get("executive_summary"):
            lines.append(f"{report['executive_summary']}\n")
        basis = report.get("decision_basis") or []
        if basis:
            lines.append("**决策依据：**")
            lines.extend(f"{idx}. {point}" for idx, point in enumerate(basis, 1))
        if match.matched_points:
            lines.append("**匹配点：**")
            lines.extend(f"- {point}" for point in match.matched_points)
        if match.missing_points:
            lines.append("\n**缺口分析：**")
            lines.extend(f"- {point}" for point in match.missing_points)
        risk = (report.get("risks") or []) + (match.risk_notes or [])
        if risk:
            lines.append("\n**风险提醒：**")
            lines.extend(f"- {point}" for point in risk)
        research_summary = report.get("research_summary") or match.research_summary or []
        if research_summary:
            lines.append("\n**深度研究补充：**")
            lines.extend(f"- {point}" for point in research_summary)
        if is_deep:
            rewrites = report.get("resume_rewrites") or []
            if rewrites:
                lines.append("\n**简历定向改写：**")
                lines.extend(f"- {point}" for point in rewrites)
            guides = report.get("interview_guides") or []
            if guides:
                lines.append("\n**高概率面试问题与回答策略：**")
                for idx, guide in enumerate(guides, 1):
                    lines.append(f"\n{idx}. **{guide.get('question', '面试问题')}**")
                    lines.append(f"   - 考察意图：{guide.get('why_asked', '验证岗位核心能力')}")
                    lines.append(f"   - 回答框架：{guide.get('answer_framework', '结合真实项目展开说明')}")
                    lines.append(f"   - 可用证据：{guide.get('evidence', '当前简历未体现')}")
            elif report.get("interview_questions"):
                lines.append("\n**面试可能问题：**")
                lines.extend(f"- {point}" for point in report.get("interview_questions") or [])
            if report.get("project_talking_points"):
                lines.append("\n**项目讲解重点：**")
                lines.extend(f"- {point}" for point in report.get("project_talking_points") or [])
            if report.get("boss_greeting"):
                lines.append(f"\n**BOSS 打招呼话术：**\n> {report['boss_greeting']}")
            if report.get("hr_message"):
                lines.append(f"\n**HR 私信：**\n> {report['hr_message']}")
            if report.get("improvement_tips"):
                lines.append("\n**短板补强建议：**")
                lines.extend(f"- {point}" for point in report.get("improvement_tips") or [])
            if report.get("questions_to_ask"):
                lines.append("\n**建议反问面试官：**")
                lines.extend(f"- {point}" for point in report.get("questions_to_ask") or [])
            if report.get("action_plan"):
                lines.append("\n**行动清单：**")
                lines.extend(f"- [ ] {point}" for point in report.get("action_plan") or [])
        else:
            if report.get("interview_focus"):
                lines.append("\n**面试准备重点：**")
                lines.extend(f"- {point}" for point in report.get("interview_focus") or [])
        lines.append("\n---\n")

    return "\n".join(lines)
