"""报告与工作流相关 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class JobReport(BaseModel):
    """Report Agent 针对单个岗位的结构化输出。"""

    conclusion: str = ""  # 岗位推荐结论
    priority: str = ""  # 投递优先级说明
    reasons: list[str] = Field(default_factory=list)  # 推荐理由
    risks: list[str] = Field(default_factory=list)  # 风险提醒
    interview_questions: list[str] = Field(default_factory=list)  # 面试可能问题
    project_talking_points: list[str] = Field(default_factory=list)  # 项目讲解重点
    boss_greeting: str = ""  # BOSS 打招呼话术
    hr_message: str = ""  # HR 私信
    improvement_tips: list[str] = Field(default_factory=list)  # 短板补习建议


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
    title: str
    summary: str
    markdown_content: str
    created_at: datetime | None = None

    class Config:
        from_attributes = True
