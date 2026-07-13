"""OCR 失败或低质量时的多模态视觉兜底。"""
from __future__ import annotations

import mimetypes

from config import get_settings
from services import llm_service


def recognize_image(image: bytes, filename: str, *, document_type: str) -> str:
    settings = get_settings()
    if not settings.role_configured("vision"):
        raise llm_service.LLMConfigError("视觉档未配置")
    mime_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
    subject = "招聘岗位 JD" if document_type == "job" else "简历"
    return llm_service.chat_image_text(
        "你是文档 OCR 校对助手。只忠实转写图片中可见的文字，不补写、不总结、不推测。",
        f"请按自然阅读顺序完整转写这张{subject}图片。保留标题、分段、列表、公司名、薪资和时间等信息；只输出转写文本。",
        image,
        mime_type=mime_type,
        model_role="vision",
    )
