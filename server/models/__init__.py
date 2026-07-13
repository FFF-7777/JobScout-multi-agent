"""ORM 模型汇总。"""
from models.agent_item_run import AgentItemRun
from models.agent_run import AgentRun
from models.job import Job, JobAnalysis
from models.job_parse_task import JobParseTask
from models.job_report import JobReport
from models.match import MatchResult
from models.report import Report
from models.report_task import ReportTask
from models.resume import Resume

__all__ = [
    "Resume",
    "Job",
    "JobAnalysis",
    "JobParseTask",
    "MatchResult",
    "AgentRun",
    "AgentItemRun",
    "Report",
    "ReportTask",
    "JobReport",
    "JobParseTask",
]
