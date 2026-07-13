import unittest
from types import SimpleNamespace
from unittest.mock import patch

from schemas.match import MatchResultModel
from schemas.job import JobProfile
from schemas.match import ApplicationDecision, CareerAlignment, GapItem, HrScreening, StrengthItem
from schemas.report import JobReport
from services import report_agent, workflow


class WorkflowRegressionTests(unittest.TestCase):
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
            match_quick_top_k=3,
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
