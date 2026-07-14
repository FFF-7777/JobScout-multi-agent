import prompts
from services import match_agent, report_agent, vision_ocr, web_research_service


def test_resume_prompt_is_evidence_bound_without_changing_schema() -> None:
    required_fields = (
        '"name"',
        '"target_roles"',
        '"skills"',
        '"projects"',
        '"strengths"',
        '"weaknesses"',
        '"graduation_year"',
        '"available_days_per_week"',
    )
    assert all(field in prompts.RESUME_SYSTEM for field in required_fields)
    assert "不得把推测当作简历事实" in prompts.RESUME_SYSTEM
    assert "未明确表达求职方向" in prompts.RESUME_SYSTEM
    assert "技能去重" in prompts.RESUME_SYSTEM
    assert "不能因为未提到某项技能就自动列为短板" in prompts.RESUME_SYSTEM


def test_job_prompt_preserves_existing_schema_and_requirement_levels() -> None:
    required_fields = (
        '"company_name"',
        '"job_title"',
        '"required_skills"',
        '"preferred_skills"',
        '"responsibilities"',
        '"requirements"',
        '"risk_tags"',
        '"jd_summary"',
    )
    assert all(field in prompts.JOB_SYSTEM for field in required_fields)
    assert "招聘者所属公司" in prompts.JOB_SYSTEM
    assert "不得把职责中提到的工具自动判定为必备技能" in prompts.JOB_SYSTEM
    assert "摘要不得补充原文没有的常见要求" in prompts.JOB_SYSTEM


def test_match_prompt_calibrates_scores_and_separates_research_from_evidence() -> None:
    assert "公司知名度" in prompts.MATCH_SYSTEM
    assert "不得直接提高匹配分" in prompts.MATCH_SYSTEM
    assert "外部研究不能代替简历证据" in prompts.MATCH_SYSTEM
    assert "fatal" in prompts.MATCH_SYSTEM and "major" in prompts.MATCH_SYSTEM
    assert "confidence" in prompts.MATCH_SYSTEM and "证据完整度" in prompts.MATCH_SYSTEM
    assert "区分“不会”“未体现”“有相近经验”" in match_agent._DEEP_REVIEW_SUFFIX


def test_report_prompt_selects_job_specific_interview_angles_without_schema_drift() -> None:
    required_fields = (
        '"interview_questions"',
        '"interview_guides"',
        '"project_talking_points"',
        '"resume_rewrites"',
        '"boss_greeting"',
        '"hr_message"',
    )
    assert all(field in prompts.REPORT_SYSTEM for field in required_fields)
    for angle in ("真实性核验", "专业硬实力", "岗位匹配", "逻辑拆解", "协作", "求职动机"):
        assert angle in prompts.REPORT_SYSTEM
    assert "不要为了覆盖分类而凑题" in prompts.REPORT_SYSTEM
    assert "一一对应" in prompts.REPORT_SYSTEM
    assert "不得使用羞辱、威胁或强迫二选一" in prompts.REPORT_SYSTEM
    assert "输入中提供的联网研究" in prompts.REPORT_SYSTEM


def test_research_and_vision_prompts_prioritize_verifiability_and_fidelity() -> None:
    source = open(web_research_service.__file__, encoding="utf-8").read()
    assert "公司官网、政府或监管公开信息、权威媒体" in source
    assert "同名公司" in source
    assert "相同顺序" in source

    vision_source = open(vision_ocr.__file__, encoding="utf-8").read()
    assert "保持原文顺序" in vision_source
    assert "不得擅自纠错" in vision_source
    assert "不得合并不同栏" in vision_source


def test_prompt_versions_are_advanced_to_invalidate_old_cache() -> None:
    assert int(match_agent.PROMPT_VERSION) >= 4
    assert int(report_agent.REPORT_PROMPT_VERSION) >= 4
