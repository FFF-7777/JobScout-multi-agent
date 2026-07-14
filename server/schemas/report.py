"""报告与工作流相关 Pydantic 模型。"""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

from schemas.match import ResearchMetadata


class InterviewGuide(BaseModel):
    question: str = ""
    why_asked: str = ""
    answer_framework: str = ""
    evidence: str = ""


class JobReport(BaseModel):
    """Report Agent 针对单个岗位的结构化输出。"""

    mode: str = "deep"
    conclusion: str = ""
    priority: str = ""
    executive_summary: str = ""
    decision_basis: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    interview_questions: list[str] = Field(default_factory=list)
    interview_guides: list[InterviewGuide] = Field(default_factory=list)
    project_talking_points: list[str] = Field(default_factory=list)
    resume_rewrites: list[str] = Field(default_factory=list)
    boss_greeting: str = ""
    hr_message: str = ""
    improvement_tips: list[str] = Field(default_factory=list)
    questions_to_ask: list[str] = Field(default_factory=list)
    action_plan: list[str] = Field(default_factory=list)
    research_metadata: ResearchMetadata = Field(default_factory=ResearchMetadata)


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: str
    agent_name: str
    step_order: int
    status: str
    summary: str
    progress: int = 0
    input_json: dict | None = None
    output_json: dict | None = None
    error_message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    eta_seconds: int = 0
    eta_low: int = 0
    eta_high: int = 0
    current_item: str = ""
    total_items: int = 0
    completed_items: int = 0
    failed_items: int = 0
    in_flight_items: list | None = None

class WorkflowRunRequest(BaseModel):
    resume_id: int
    job_ids: list[int] = Field(default_factory=list)


class WorkflowTaskOut(BaseModel):
    task_id: str
    status: str
    steps: list[AgentRunOut] = Field(default_factory=list)


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    resume_id: int
    task_id: str | None = None
    mode: str = "standard"
    title: str
    summary: str
    markdown_content: str
    created_at: datetime | None = None
