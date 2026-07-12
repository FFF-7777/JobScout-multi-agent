"""Report Agent：生成单个岗位的投递建议与面试准备，并可汇总为 Markdown 报告。"""
from __future__ import annotations

import json

import prompts
from schemas.job import JobProfile
from schemas.match import MatchResultModel
from schemas.report import JobReport
from schemas.resume import ResumeProfile
from services import llm_service


def run(
    resume: ResumeProfile, job: JobProfile, match: MatchResultModel
) -> JobReport:
    data = llm_service.chat_json(
        prompts.REPORT_SYSTEM,
        prompts.REPORT_USER.format(
            resume_profile=json.dumps(resume.model_dump(), ensure_ascii=False),
            job_profile=json.dumps(job.model_dump(), ensure_ascii=False),
            match_result=json.dumps(match.model_dump(), ensure_ascii=False),
        ),
    )
    return JobReport.model_validate(data)


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
