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
REPORT_PROMPT_VERSION = "2"


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
    strengths = [
        {
            "title": item.title,
            "evidence": item.resume_evidence,
            "relevance": item.job_relevance,
        }
        for item in match.top_strengths
    ]
    gaps = [item.model_dump() for item in match.main_gaps]
    interview_focus = [
        f"围绕“{item.title}”准备 2 分钟案例：说明背景、个人职责、关键决策和结果；可用证据：{item.resume_evidence or '当前简历未提供'}"
        for item in match.top_strengths[:3]
    ]
    if not interview_focus:
        interview_focus = [
            "回顾简历中与岗位要求最贴近的项目，准备讲清你做了什么、用了什么技术、结果如何"
        ]
    return {
        "conclusion": match.recommendation,
        "priority": f"匹配度 {match.score} 分（{match.level} 级）",
        "executive_summary": match.application_decision.summary or match.recommendation,
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
        "action_plan": match.next_actions or [g["action"] for g in gaps if g.get("action")][:3],
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
    lines: list[str] = [
        f"# 岗位投递决策报告｜基础分析",
        f"**候选人：** {resume.name or '未命名候选人'}　　**分析岗位：** {len(items)} 个",
        "",
        "> 基础分析基于简历、岗位画像和匹配证据生成，重点回答“是否值得投、凭什么、投递前要做什么”。",
        "",
        "## 决策总览",
        "",
        "| 排名 | 公司与岗位 | 地点 / 薪资 | 匹配度 | 决策 |",
        "| ---: | --- | --- | ---: | --- |",
    ]
    for i, it in enumerate(items, 1):
        j: JobProfile = it["job"]
        m: MatchResultModel = it["match"]
        lines.append(
            f"| {i} | **{j.company_name or '未知公司'}**<br>{j.job_title or '未知岗位'} "
            f"| {j.city or '未注明'} / {j.salary or '未注明'} | **{m.score}**（{m.level}） | {m.recommendation} |"
        )

    dimension_labels = {
        "tech_stack": "技术栈",
        "project_exp": "项目经验",
        "role_direction": "岗位方向",
        "qualification": "资格条件",
        "logistics": "地点与薪资",
    }
    for i, it in enumerate(items, 1):
        j: JobProfile = it["job"]
        m: MatchResultModel = it["match"]
        r: dict = it["report"]
        screening = r.get("screening") or {}
        lines.extend([
            "",
            "---",
            "",
            f"## {i}. {j.company_name or '未知公司'}｜{j.job_title or '未知岗位'}",
            "",
            f"> **{r.get('conclusion') or m.recommendation}**　·　{r.get('priority', '')}",
            "",
            r.get("executive_summary") or m.recommendation,
            "",
            "### 决策依据",
            "",
            "| HR 初筛 | 职业方向 | 评估置信度 |",
            "| --- | ---: | ---: |",
            f"| {screening.get('reason') or '暂无额外说明'} | {screening.get('career_score', '—')} | {screening.get('confidence', '—')}% |",
            "",
            "| 评估维度 | 得分 | 说明 |",
            "| --- | ---: | --- |",
        ])
        dimensions = r.get("dimensions") or {}
        for key, label in dimension_labels.items():
            score = dimensions.get(key, "—")
            note = "优势项" if isinstance(score, (int, float)) and score >= 75 else "需要补充证据" if isinstance(score, (int, float)) and score < 60 else "基本匹配"
            lines.append(f"| {label} | {score} | {note} |")

        lines.extend(["", "### 核心胜算", ""])
        strengths = r.get("strengths") or []
        if strengths:
            for index, strength in enumerate(strengths, 1):
                lines.append(f"**{index}. {strength.get('title', '匹配优势')}**")
                lines.append(f"- 简历证据：{strength.get('evidence') or '当前简历未提供明确证据'}")
                lines.append(f"- 岗位关联：{strength.get('relevance') or '需结合岗位要求进一步核实'}")
        else:
            lines.append("当前材料中没有形成明显竞争优势，建议先补强简历证据再投递。")

        lines.extend(["", "### 关键缺口与影响", ""])
        gaps = r.get("gaps") or []
        if gaps:
            for index, gap in enumerate(gaps, 1):
                severity = {"fatal": "高风险", "major": "主要缺口", "minor": "次要缺口"}.get(gap.get("severity"), "待核实")
                lines.append(f"**{index}. {gap.get('title', '能力缺口')}｜{severity}**")
                lines.append(f"- 对投递的影响：{gap.get('impact') or '需进一步核实'}")
                lines.append(f"- 处理建议：{gap.get('action') or '补充相关证据并准备解释'}")
        else:
            lines.append("未识别到明确的主要缺口。")

        lines.extend(["", "### 投递前行动清单", ""])
        actions = r.get("action_plan") or []
        lines.extend(f"- [ ] {action}" for action in actions)
        if not actions:
            lines.append("- [ ] 核对岗位硬性条件，并为最相关项目准备可验证的结果说明。")

        lines.extend(["", "### 面试准备重点", ""])
        lines.extend(f"{index}. {focus}" for index, focus in enumerate(r.get("interview_focus") or [], 1))

    return "\n".join(lines).strip() + "\n"


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
    lines.append(f"# 岗位投递决策报告｜{'深度分析' if has_deep else '基础分析'}\n")
    lines.append(f"**候选人：** {resume.name or '未命名候选人'}　　**分析岗位：** {len(items)} 个\n")
    lines.append("> 本报告围绕投递决策、简历表达和面试胜率展开；所有结论均以当前简历与岗位信息为依据。\n")
    lines.append("## 候选人定位\n")
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
        lines.append(f"> **{r.get('conclusion', m.recommendation)}**　·　{r.get('priority', f'{m.score} 分 / {m.level} 级')}\n")
        if r.get("executive_summary"):
            lines.append(f"{r['executive_summary']}\n")
        basis = r.get("decision_basis") or []
        if basis:
            lines.append("**决策依据：**")
            lines.extend(f"{idx}. {point}" for idx, point in enumerate(basis, 1))
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
            rewrites = r.get("resume_rewrites") or []
            if rewrites:
                lines.append("\n**简历定向改写：**")
                lines.extend(f"- {p}" for p in rewrites)
            guides = r.get("interview_guides") or []
            if guides:
                lines.append("\n**高概率面试问题与回答策略：**")
                for idx, guide in enumerate(guides, 1):
                    lines.append(f"\n{idx}. **{guide.get('question', '面试问题')}**")
                    lines.append(f"   - 考察意图：{guide.get('why_asked', '验证岗位核心能力')}")
                    lines.append(f"   - 回答框架：{guide.get('answer_framework', '结合真实项目说明')}")
                    lines.append(f"   - 可用证据：{guide.get('evidence', '当前简历未体现')}")
            iq = r.get("interview_questions") or []
            if iq and not guides:
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
            questions = r.get("questions_to_ask") or []
            if questions:
                lines.append("\n**建议反问面试官：**")
                lines.extend(f"- {p}" for p in questions)
            actions = r.get("action_plan") or []
            if actions:
                lines.append("\n**行动清单：**")
                lines.extend(f"- [ ] {p}" for p in actions)
        else:
            ifv = r.get("interview_focus") or []
            if ifv:
                lines.append("\n**面试准备重点：**")
                lines.extend(f"- {p}" for p in ifv)
        lines.append("\n---\n")

    return "\n".join(lines)
