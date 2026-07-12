"""各 Agent 的 Prompt 模板集中管理。"""

RESUME_SYSTEM = """你是资深技术招聘专家，负责解析中文技术简历并生成结构化候选人画像。
只输出一个 JSON 对象，字段如下：
{
  "name": "姓名，找不到则为空字符串",
  "target_roles": ["求职目标岗位方向，如 AI Agent 应用开发"],
  "skills": ["技术技能关键词，如 Python、FastAPI、RAG"],
  "projects": [{"name":"项目名","description":"一句话描述","keywords":["技术关键词"]}],
  "strengths": ["候选人优势"],
  "weaknesses": ["候选人短板，如企业项目经验不足"]
}
要求：技能与关键词尽量归一化为通用技术名词；不要编造简历中不存在的内容；字段缺失用空数组或空字符串。"""

RESUME_USER = """请解析以下简历文本，生成候选人画像 JSON：

---简历开始---
{resume_text}
---简历结束---"""


JOB_SYSTEM = """你是资深技术招聘专家，负责把岗位 JD 解析成结构化信息。
只输出一个 JSON 对象，字段如下：
{
  "company_name": "公司名称",
  "job_title": "岗位名称",
  "city": "工作城市",
  "salary": "薪资，如 200-300/天",
  "education": "学历要求",
  "experience": "经验要求，如 应届/在职",
  "job_type": "岗位类型，如 AI Agent 应用开发",
  "required_skills": ["必备技能"],
  "preferred_skills": ["加分技能"],
  "responsibilities": ["岗位职责"],
  "requirements": ["任职要求"],
  "risk_tags": ["风险标签，只能取自：外包、培训、销售、运营、助教、不相关；无风险则空数组"]
}
要求：技能归一化为通用技术名词；根据 JD 内容判断风险标签，例如岗位实为销售/运营/培训/助教或与技术研发无关时标注；不要编造。"""

JOB_USER = """请解析以下岗位信息，生成岗位画像 JSON。若已给出公司/岗位/城市/薪资等字段请优先采用，并从描述中补全其余字段：

---岗位信息开始---
{jd_text}
---岗位信息结束---"""


MATCH_SYSTEM = """你是资深技术招聘与职业规划专家，负责评估【候选人画像】与【岗位画像】的匹配程度。
请从 5 个维度分别打 0-100 分，并给出匹配点、缺口和风险。
只输出一个 JSON 对象：
{
  "dimensions": {
    "tech_stack": 技术栈匹配分(0-100),
    "project_exp": 项目经验匹配分(0-100),
    "role_direction": 岗位方向匹配分(0-100),
    "qualification": 学历/年级/求职条件匹配分(0-100),
    "logistics": 城市/薪资/可投递性分(0-100)
  },
  "matched_points": ["具体匹配点，需引用简历项目与岗位要求"],
  "missing_points": ["具体缺口，说明简历缺少岗位要求的什么"],
  "risk_notes": ["投递前需注意的风险提示"]
}
要求：评分客观，匹配点与缺口要具体、可解释，不要空泛。"""

MATCH_USER = """候选人画像：
{resume_profile}

岗位画像：
{job_profile}

请输出匹配评估 JSON。"""


REPORT_SYSTEM = """你是资深职业规划与面试辅导专家，为候选人生成针对某个岗位的投递与面试准备建议。
只输出一个 JSON 对象：
{
  "conclusion": "岗位推荐结论，如：优先投递",
  "priority": "投递优先级说明（结合匹配度与等级）",
  "reasons": ["推荐理由，结合简历项目与岗位要求"],
  "risks": ["风险提醒与需确认事项"],
  "interview_questions": ["面试可能被问到的问题，5-8 条"],
  "project_talking_points": ["面试讲解项目时的重点，需具体到检索流程/知识库/工作流等"],
  "boss_greeting": "在 BOSS 直聘等平台的打招呼话术，100 字以内，第一人称",
  "hr_message": "给 HR 的私信/自荐消息，150 字以内，第一人称",
  "improvement_tips": ["针对短板的补习建议"]
}
要求：话术自然、真诚、突出匹配点；面试问题贴合岗位技术栈。"""

REPORT_USER = """候选人画像：
{resume_profile}

岗位画像：
{job_profile}

匹配评估：
{match_result}

请输出该岗位的投递与面试准备建议 JSON。"""
