"""硬条件预筛：在 Match Agent（LLM）之前用确定性规则过滤明显不符合的岗位。

为什么放在 LLM 之前：
- 毕业年份、每周实习天数等是硬约束，用代码判断比让模型判断更稳定、可解释。
- 不通过的岗位直接跳过 LLM 调用，省成本、省时间。

当前实现的硬条件（均来自简历与岗位的结构化字段，判定无歧义）：
1. 毕业年份：岗位要求 graduation_years 且简历有 graduation_year 时，必须命中其一。
2. 每周实习天数：岗位要求 internship_days_per_week 且简历有 available_days_per_week 时，
   简历可用天数必须 >= 岗位要求。

后续可扩展：城市是否接受、学历是否达标、薪资是否低于期望下限等（需简历侧有对应字段）。
这些硬条件只做「否定」判定——命中任一条即判不通过；不命中不代表一定匹配，
最终匹配质量仍由 Match Agent 的维度分决定。
"""
from __future__ import annotations

from schemas.job import JobProfile
from schemas.resume import ResumeProfile


def precheck_job(resume: ResumeProfile, job: JobProfile) -> dict:
    """返回 {"passed": bool, "hard_failures": [str, ...], "items": [dict, ...]}。

    items 中每个元素为 {"name", "status", "resume_evidence", "job_requirement"}，
    供下游 Match Agent 的 hard_condition_result 使用。
    """
    hard_failures: list[str] = []
    items: list[dict] = []

    # 1. 毕业年份
    if job.graduation_years and resume.graduation_year:
        item = {
            "name": "毕业年份",
            "status": "pass" if resume.graduation_year in job.graduation_years else "fail",
            "resume_evidence": f"{resume.graduation_year}届",
            "job_requirement": f"要求毕业年份 {job.graduation_years}",
        }
        items.append(item)
        if resume.graduation_year not in job.graduation_years:
            hard_failures.append(
                f"毕业年份不符（简历 {resume.graduation_year} 不在岗位要求 {job.graduation_years} 内）"
            )

    # 2. 每周实习天数
    if job.internship_days_per_week and resume.available_days_per_week is not None:
        ok = resume.available_days_per_week >= job.internship_days_per_week
        item = {
            "name": "每周实习天数",
            "status": "pass" if ok else "fail",
            "resume_evidence": f"{resume.available_days_per_week}天/周",
            "job_requirement": f"要求至少 {job.internship_days_per_week}天/周",
        }
        items.append(item)
        if not ok:
            hard_failures.append(
                f"每周实习天数不足（简历 {resume.available_days_per_week} 天 < 岗位要求 {job.internship_days_per_week} 天）"
            )

    return {"passed": not hard_failures, "hard_failures": hard_failures, "items": items}
