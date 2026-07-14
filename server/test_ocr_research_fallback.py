import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from schemas.job import JobProfile
from schemas.report import WorkflowRunRequest
from services import llm_service, ocr_pipeline, research_router, vision_ocr, web_research_service
from services.research_router import ResearchPlan


def test_vision_ocr_uses_multimodal_model() -> None:
    settings = SimpleNamespace(role_configured=lambda role: role == "vision")
    with (
        patch.object(vision_ocr, "get_settings", return_value=settings),
        patch.object(vision_ocr.llm_service, "chat_image_text", return_value="识别后的文字") as call,
    ):
        text = vision_ocr.recognize_image(b"image", "resume.png", document_type="resume")

    assert text == "识别后的文字"
    assert call.call_args.kwargs["model_role"] == "vision"
    assert call.call_args.kwargs["mime_type"] == "image/png"


def test_web_research_records_success_metadata() -> None:
    plan = ResearchPlan(enabled=True, strategy="auto", reason="需要研究", queries=["公司 岗位 技术栈"])
    settings = SimpleNamespace(role_configured=lambda role: role == "reasoning")
    with (
        patch.object(web_research_service, "get_settings", return_value=settings),
        patch.object(
            web_research_service.llm_service,
            "chat_json_with_search_metadata",
            return_value=(
                {"summary_items": ["研究摘要"], "source_notes": ["来源说明"]},
                {
                    "performed": True,
                    "sources": [{"title": "来源", "url": "https://example.com"}],
                    "provider": "dashscope",
                    "verifiable": True,
                },
            ),
        ),
    ):
        result = web_research_service.fetch_research_context(JobProfile(), plan=plan)

    assert result.status == "success"
    assert result.attempted is True
    assert result.summary_items == ["研究摘要"]
    assert result.source_notes == ["来源说明"]
    assert result.sources[0]["url"] == "https://example.com"
    assert result.verifiable is True


def test_web_research_failure_is_observable_and_degraded() -> None:
    plan = ResearchPlan(enabled=True, strategy="auto", reason="需要研究", queries=["检索词"])
    settings = SimpleNamespace(role_configured=lambda role: role == "reasoning")
    with (
        patch.object(web_research_service, "get_settings", return_value=settings),
        patch.object(
            web_research_service.llm_service,
            "chat_json_with_search_metadata",
            side_effect=RuntimeError("search unavailable"),
        ),
    ):
        result = web_research_service.fetch_research_context(JobProfile(), plan=plan)

    assert result.status == "degraded"
    assert result.attempted is True
    assert result.queries == ["检索词"]
    assert "search unavailable" in result.error


def test_dashscope_responses_extracts_verifiable_sources() -> None:
    response = SimpleNamespace(
        output_text='{"summary_items":["摘要"],"source_notes":[]}',
        model_dump=lambda: {
            "output": [
                {"type": "web_search_call", "status": "completed"},
                {
                    "type": "message",
                    "content": [
                        {
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://example.com/source",
                                    "title": "可核验来源",
                                }
                            ]
                        }
                    ],
                },
            ]
        },
    )
    client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **kwargs: response)
    )
    settings = SimpleNamespace(
        resolve_provider=lambda role: "dashscope",
        resolve_model=lambda role: "qwen3.7-max",
        llm_fast_enable_thinking=False,
        llm_reasoning_enable_thinking=False,
        llm_report_enable_thinking=False,
    )
    with (
        patch.object(llm_service, "get_settings", return_value=settings),
        patch.object(llm_service, "_get_client", return_value=client),
    ):
        data, meta = llm_service.chat_json_with_search_metadata("system", "user")

    assert data["summary_items"] == ["摘要"]
    assert meta["performed"] is True
    assert meta["verifiable"] is True
    assert meta["sources"][0]["url"] == "https://example.com/source"


def test_ocr_pipeline_falls_back_only_failed_items() -> None:
    settings = SimpleNamespace(
        tencent_ocr_concurrency=4,
        tencent_ocr_rate_per_sec=4,
        baidu_ocr_concurrency=2,
        baidu_ocr_rate_per_sec=2,
        vision_ocr_concurrency=1,
    )

    async def tencent_batch(images, **kwargs):
        return [
            {"filename": images[0][1], "text": "腾讯成功文本"},
            {"filename": images[1][1], "error": "腾讯失败"},
        ]

    async def baidu_batch(images, **kwargs):
        assert [item[1] for item in images] == ["b.png"]
        return [{"filename": "b.png", "text": "百度成功文本"}]

    with (
        patch.object(ocr_pipeline, "get_settings", return_value=settings),
        patch.object(ocr_pipeline.tencent_ocr, "recognize_images_batch", side_effect=tencent_batch),
        patch.object(ocr_pipeline.baidu_ocr, "recognize_images_batch", side_effect=baidu_batch),
        patch.object(ocr_pipeline.vision_ocr, "recognize_image") as vision,
    ):
        result = asyncio.run(
            ocr_pipeline.recognize_images(
                [(b"a", "a.png"), (b"b", "b.png")], document_type="job"
            )
        )

    assert result[0]["audit"]["final_provider"] == "tencent"
    assert result[1]["audit"]["final_provider"] == "baidu"
    vision.assert_not_called()


def test_workflow_request_has_no_user_research_strategy() -> None:
    request = WorkflowRunRequest(resume_id=1, research_strategy="off")

    assert not hasattr(request, "research_strategy")


def test_analysis_mode_owns_research_policy() -> None:
    settings = SimpleNamespace(
        deep_research_max_items=6,
    )
    job = JobProfile(company_name="示例公司", job_title="Python 工程师", city="深圳")
    with patch.object(research_router, "get_settings", return_value=settings):
        quick = research_router.build_research_plan(
            job, tier="quick", analyze_mode="summary"
        )
        forced = research_router.build_research_plan(
            job, tier="deep", analyze_mode="full"
        )

    assert quick.enabled is False
    assert quick.strategy == "off"
    assert forced.enabled is True
    assert forced.strategy == "force"


def test_fast_role_never_enables_thinking() -> None:
    settings = SimpleNamespace(
        llm_fast_enable_thinking=True,
        llm_reasoning_enable_thinking=True,
        llm_report_enable_thinking=True,
    )

    with patch.object(llm_service, "get_settings", return_value=settings):
        assert llm_service._resolve_enable_thinking("fast") is False
