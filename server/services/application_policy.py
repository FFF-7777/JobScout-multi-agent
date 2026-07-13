"""投递策略判定。"""
from __future__ import annotations

from schemas.match import ApplicationDecision, GapItem


def decide_application(
    *,
    score: float,
    hard_fail: bool,
    career_score: float,
    role_direction_score: float,
    main_gaps: list[GapItem],
) -> ApplicationDecision:
    fatal_count = sum(1 for gap in main_gaps if gap.severity == "fatal")
    major_count = sum(1 for gap in main_gaps if gap.severity == "major")

    reasons: list[str] = []
    if hard_fail:
        reasons.append("存在硬性条件不满足，继续投递通过率很低。")
    if fatal_count:
        reasons.append(f"有 {fatal_count} 个致命缺口，会直接影响初筛或面试通过率。")
    if major_count:
        reasons.append(f"有 {major_count} 个主要缺口，需要在投递前补充证据或准备解释。")
    if career_score < 40 or role_direction_score < 30:
        reasons.append("岗位方向与当前职业路线偏差较大。")
    elif career_score >= 70 and role_direction_score >= 70:
        reasons.append("岗位方向与候选人的目标路径基本一致。")
    if score >= 80:
        reasons.append("综合匹配度高，可以优先投入时间。")
    elif score < 55:
        reasons.append("综合匹配度偏低，不建议作为主投目标。")

    if hard_fail or fatal_count >= 2:
        action = "skip"
    elif career_score < 40 or role_direction_score < 30:
        action = "selective_apply" if score >= 60 and fatal_count == 0 else "skip"
    elif score >= 82 and fatal_count == 0:
        action = "priority_apply"
    elif score >= 68 and fatal_count == 0:
        action = "apply"
    elif score >= 52:
        action = "selective_apply"
    else:
        action = "skip"

    summary = {
        "priority_apply": f"匹配度 {score} 分，核心方向一致，建议优先投递。",
        "apply": f"匹配度 {score} 分，主干能力可支撑岗位要求，建议正常投递。",
        "selective_apply": f"匹配度 {score} 分，存在明显缺口，建议选择性投递。",
        "skip": f"匹配度 {score} 分，方向或硬条件不理想，当前不建议投入投递成本。",
    }[action]

    return ApplicationDecision(action=action, summary=summary, reasons=reasons[:4])
