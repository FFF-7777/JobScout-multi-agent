from types import SimpleNamespace
from unittest.mock import patch

from schemas.job import JobProfile
from services import vision_ocr, web_research_service
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
            "chat_json",
            return_value={"summary_items": ["研究摘要"], "source_notes": ["来源说明"]},
        ),
    ):
        result = web_research_service.fetch_research_context(JobProfile(), plan=plan)

    assert result.status == "success"
    assert result.attempted is True
    assert result.summary_items == ["研究摘要"]
    assert result.source_notes == ["来源说明"]


def test_web_research_failure_is_observable_and_degraded() -> None:
    plan = ResearchPlan(enabled=True, strategy="auto", reason="需要研究", queries=["检索词"])
    settings = SimpleNamespace(role_configured=lambda role: role == "reasoning")
    with (
        patch.object(web_research_service, "get_settings", return_value=settings),
        patch.object(web_research_service.llm_service, "chat_json", side_effect=RuntimeError("search unavailable")),
    ):
        result = web_research_service.fetch_research_context(JobProfile(), plan=plan)

    assert result.status == "degraded"
    assert result.attempted is True
    assert result.queries == ["检索词"]
    assert "search unavailable" in result.error
