import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main as main_app
from database import Base
from models import (
    AgentRun,
    Job,
    JobAnalysis,
    JobParseTask,
    JobReport as JobReportRow,
    MatchResult,
    Report,
    ReportTask,
    Resume,
)
from routers import jobs as jobs_router
from routers import resumes as resumes_router
from schemas.match import MatchResultModel
from schemas.job import JobProfile
from schemas.job import JobImportUrlRequest
from schemas.match import ApplicationDecision, CareerAlignment, GapItem, HrScreening, StrengthItem
from schemas.report import JobReport
from services import job_parse_queue, report_agent, workflow


class WorkflowRegressionTests(unittest.TestCase):
    def make_session_factory(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)

    def test_llm_report_is_persisted_as_deep_mode(self):
        self.assertEqual(JobReport().mode, "deep")

    def test_standard_report_contains_evidence_and_action_plan(self):
        match = MatchResultModel(
            score=76,
            level="B",
            recommendation="建议投递",
            top_strengths=[
                StrengthItem(
                    title="RAG 项目经验",
                    resume_evidence="项目使用 LangChain 构建检索链路",
                    job_relevance="岗位要求 RAG 应用开发",
                )
            ],
            main_gaps=[
                GapItem(
                    title="缺少评测经验",
                    severity="major",
                    impact="岗位要求建立评测体系",
                    short_term_fixable=True,
                    action="补充离线评测方案并写入项目说明",
                )
            ],
            next_actions=["把检索准确率与响应耗时补充到简历项目中"],
            hr_screening=HrScreening(likely_result="borderline", main_reason="项目证据较少"),
            career_alignment=CareerAlignment(score=82, analysis="方向一致"),
            application_decision=ApplicationDecision(action="apply", summary="核心方向匹配"),
        )

        report = report_agent.build_standard_job_report(JobProfile(job_title="AI 应用开发"), match)

        self.assertEqual(report["mode"], "standard")
        self.assertEqual(report["strengths"][0]["evidence"], "项目使用 LangChain 构建检索链路")
        self.assertEqual(report["gaps"][0]["action"], "补充离线评测方案并写入项目说明")
        self.assertEqual(report["action_plan"][0], "把检索准确率与响应耗时补充到简历项目中")

    def test_summary_only_match_finishes_without_deep_phase(self):
        updates = []

        class Query:
            def filter(self, *args, **kwargs):
                return self

            def all(self):
                return []

        class Session:
            def query(self, *args, **kwargs):
                return Query()

            def close(self):
                pass

        settings = SimpleNamespace(
            match_two_tier=True,
            match_agent_concurrency=1,
        )
        outcome = SimpleNamespace(
            match=MatchResultModel(score=80, level="A"),
            cache_hit=False,
            key="cache-key",
            error="",
        )

        def bounded_map(items, worker, *, max_concurrency, on_result):
            for item in items:
                on_result(item, worker(item), None)

        patches = (
            patch.object(workflow, "get_settings", return_value=settings),
            patch.object(workflow, "SessionLocal", Session),
            patch.object(workflow, "_update_run", side_effect=lambda *args, **kwargs: updates.append(kwargs)),
            patch.object(workflow, "_calc_eta", return_value=0),
            patch.object(workflow, "_calc_eta_range", return_value=(0, 0)),
            patch.object(workflow, "_is_aborted", return_value=False),
            patch.object(workflow, "upsert_item_run", return_value=None),
            patch.object(workflow, "bounded_map", side_effect=bounded_map),
            patch.object(workflow.match_core, "run_single_match", return_value=outcome),
            patch.object(workflow.match_core, "persist_match_row", return_value=7),
        )
        for mocked in patches:
            mocked.start()
            self.addCleanup(mocked.stop)

        state = {
            "task_id": "task-1",
            "resume_id": 1,
            "resume_profile": {},
            "jobs_parsed": [{"job_id": 11, "profile": {"job_title": "测试岗位"}}],
            "errors": [],
        }

        result = workflow.node_match_jobs(state)

        self.assertEqual(result["match_results"][0]["job_id"], 11)
        self.assertEqual(updates[-1]["status"], "success")
        self.assertEqual(updates[-1]["progress"], 100)
        self.assertIs(updates[-1]["finish"], True)

    def test_disabling_two_tier_uses_reasoning_for_every_job(self):
        tiers = []
        settings = SimpleNamespace(
            match_two_tier=False,
            match_agent_concurrency=1,
        )
        outcome = SimpleNamespace(
            match=MatchResultModel(score=80, level="A"),
            cache_hit=False,
            key="cache-key",
            error="",
        )

        def bounded_map(items, worker, *, max_concurrency, on_result):
            for item in items:
                on_result(item, worker(item), None)

        def run_single_match(*args, tier, **kwargs):
            tiers.append(tier)
            return outcome

        with (
            patch.object(workflow, "get_settings", return_value=settings),
            patch.object(workflow, "_update_run"),
            patch.object(workflow, "_calc_eta", return_value=0),
            patch.object(workflow, "_calc_eta_range", return_value=(0, 0)),
            patch.object(workflow, "_is_aborted", return_value=False),
            patch.object(workflow, "upsert_item_run"),
            patch.object(workflow, "bounded_map", side_effect=bounded_map),
            patch.object(workflow.match_core, "run_single_match", side_effect=run_single_match),
            patch.object(workflow.match_core, "persist_match_row", return_value=7),
        ):
            workflow.node_match_jobs(
                {
                    "task_id": "task-reasoning",
                    "resume_id": 1,
                    "resume_profile": {},
                    "jobs_parsed": [{"job_id": 11, "profile": {"job_title": "测试岗位"}}],
                    "errors": [],
                }
            )

        self.assertEqual(tiers, ["deep"])

    def test_parse_failure_marks_job_failed(self):
        Session = self.make_session_factory()
        db = Session()
        job = Job(jd_text="测试 JD", parse_status="parsing")
        db.add(job)
        db.flush()
        task = JobParseTask(job_id=job.id, status="running")
        db.add(task)
        db.commit()
        job_id, task_id = job.id, task.id
        db.close()

        with (
            patch.object(job_parse_queue, "SessionLocal", Session),
            patch.object(job_parse_queue, "_do_parse", side_effect=RuntimeError("模型失败")),
        ):
            job_parse_queue._parse_single((job_id, task_id))

        check = Session()
        self.assertEqual(check.get(JobParseTask, task_id).status, "failed")
        self.assertEqual(check.get(Job, job_id).parse_status, "failed")
        self.assertIn("模型失败", check.get(Job, job_id).parse_error)
        check.close()

    def test_restart_recovers_agent_and_report_tasks(self):
        Session = self.make_session_factory()
        db = Session()
        db.add_all(
            [
                AgentRun(task_id="task-a", agent_name="Resume Agent", status="running"),
                AgentRun(task_id="task-a", agent_name="Job Agent", status="pending"),
                ReportTask(task_id="report-a", mode="deep", status="running", total=1),
                ReportTask(task_id="report-b", mode="deep", status="queued", total=1),
            ]
        )
        db.commit()
        db.close()

        with patch.object(main_app, "SessionLocal", Session):
            main_app._recover_interrupted_runs()

        check = Session()
        self.assertEqual({r.status for r in check.query(AgentRun).all()}, {"failed"})
        self.assertTrue(all(r.finished_at is not None for r in check.query(AgentRun).all()))
        self.assertEqual({r.status for r in check.query(ReportTask).all()}, {"failed"})
        self.assertTrue(all(r.finished_at is not None for r in check.query(ReportTask).all()))
        check.close()

    def test_deleting_job_removes_all_dependent_match_reports(self):
        Session = self.make_session_factory()
        db = Session()
        resume = Resume(filename="resume.md", raw_text="x")
        job = Job(jd_text="jd")
        db.add_all([resume, job])
        db.flush()
        result = MatchResult(resume_id=resume.id, job_id=job.id, task_id="task-a")
        db.add(result)
        db.flush()
        db.add_all(
            [
                JobAnalysis(job_id=job.id),
                JobReportRow(match_result_id=result.id, mode="standard"),
                JobParseTask(
                    job_id=job.id,
                    status="done",
                    finished_at=datetime.now(UTC).replace(tzinfo=None),
                ),
            ]
        )
        db.commit()
        job_id = job.id

        jobs_router.delete_job(job_id, db)

        self.assertEqual(db.query(MatchResult).count(), 0)
        self.assertEqual(db.query(JobReportRow).count(), 0)
        self.assertEqual(db.query(JobParseTask).count(), 0)
        db.close()

    def test_deleting_resume_removes_reports_and_aggregates(self):
        Session = self.make_session_factory()
        db = Session()
        resume = Resume(filename="resume.md", raw_text="x")
        job = Job(jd_text="jd")
        db.add_all([resume, job])
        db.flush()
        result = MatchResult(resume_id=resume.id, job_id=job.id, task_id="task-a")
        db.add(result)
        db.flush()
        db.add(JobReportRow(match_result_id=result.id, mode="standard"))
        db.add(Report(resume_id=resume.id, title="报告"))
        db.commit()
        resume_id = resume.id

        resumes_router.delete_resume(resume_id, db)

        self.assertEqual(db.query(MatchResult).count(), 0)
        self.assertEqual(db.query(JobReportRow).count(), 0)
        self.assertEqual(db.query(Report).count(), 0)
        db.close()

    def test_import_url_allows_zhaopin_and_enqueues_parse(self):
        Session = self.make_session_factory()
        db = Session()

        with (
            patch.object(jobs_router.url_fetcher, "fetch", return_value="职位描述正文"),
            patch.object(jobs_router, "enqueue_parse") as enqueue_mock,
        ):
            out = jobs_router.import_url(
                JobImportUrlRequest(url="https://www.zhaopin.com/jobdetail/CCL1501502560J40873812214.htm"),
                db,
            )

        self.assertEqual(out["source"], "url")
        self.assertEqual(out["job_url"], "https://www.zhaopin.com/jobdetail/CCL1501502560J40873812214.htm")
        enqueue_mock.assert_called_once()
        self.assertEqual(db.query(Job).count(), 1)
        db.close()

    def test_import_url_guides_boss_users_to_manual_or_ocr(self):
        Session = self.make_session_factory()
        db = Session()

        with self.assertRaises(HTTPException) as ctx:
            jobs_router.import_url(
                JobImportUrlRequest(url="https://www.zhipin.com/job_detail/example.html"),
                db,
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("仅稳定支持智联招聘链接导入", ctx.exception.detail)
        self.assertIn("BOSS直聘", ctx.exception.detail)
        self.assertIn("截图 OCR", ctx.exception.detail)
        self.assertEqual(db.query(Job).count(), 0)
        db.close()

    def test_unexpected_workflow_error_marks_unfinished_steps_failed(self):
        captured = []
        with (
            patch.object(workflow._GRAPH, "invoke", side_effect=RuntimeError("boom")),
            patch.object(
                workflow,
                "_fail_unfinished_runs",
                side_effect=lambda task_id, error: captured.append((task_id, error)),
                create=True,
            ),
        ):
            workflow.run_workflow("task-2", 1, [11])

        self.assertEqual(captured, [("task-2", "boom")])


if __name__ == "__main__":
    unittest.main()
