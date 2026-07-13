"""报告与工作流相关 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class JobReport(BaseModel):
    """Report Agent 针对单个岗位的结构化输出。"""

    mode: str = "deep"
    conclusion: str = ""  # 岗位推荐结论
    priority: str = ""  # 投递优先级说明
    reasons: list[str] = Field(default_factory=list)  # 推荐理由
    risks: list[str] = Field(default_factory=list)  # 风险提醒
    interview_questions: list[str] = Field(default_factory=list)  # 面试可能问题
    project_talking_points: list[str] = Field(default_factory=list)  # 项目讲解重点
    boss_greeting: str = ""  # BOSS 打招呼话术
    hr_message: str = ""  # HR 私信
    improvement_tips: list[str] = Field(default_factory=list)  # 短板补习建议
    executive_summary: str = ""  # 一句话说明为什么投/不投
    decision_basis: list[str] = Field(default_factory=list)  # 决策依据，必须引用输入证据
    interview_guides: list[dict] = Field(default_factory=list)  # 问题、考察点、回答框架、可用证据
    resume_rewrites: list[str] = Field(default_factory=list)  # 针对该岗位的简历改写建议
    questions_to_ask: list[str] = Field(default_factory=list)  # 候选人反问面试官的问题
    action_plan: list[str] = Field(default_factory=list)  # 按优先级排序的投递前行动


class AgentRunOut(BaseModel):
    id: int
    task_id: str
    agent_name: str
    step_order: int
    status: str
    summary: str
    progress: int = 0
    output_json: dict | None = None
    error_message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    eta_seconds: int = 0
    # ETA 范围（基于 P50/P90）：前端展示「约 X~Y 分钟」
    eta_low: int = 0
    eta_high: int = 0
    current_item: str = ""
    # 单岗位并发可视化计数
    total_items: int = 0
    completed_items: int = 0
    failed_items: int = 0
    in_flight_items: list | None = None

    class Config:
        from_attributes = True


class WorkflowRunRequest(BaseModel):
    resume_id: int
    job_ids: list[int] = Field(default_factory=list)


class WorkflowTaskOut(BaseModel):
    task_id: str
    status: str
    steps: list[AgentRunOut] = Field(default_factory=list)


class ReportOut(BaseModel):
    id: int
    resume_id: int
    task_id: str | None = None
    mode: str = "standard"
    title: str
    summary: str
    markdown_content: str
    created_at: datetime | None = None

    class Config:
        from_attributes = True
