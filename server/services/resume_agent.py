"""Resume Agent：解析简历文本 -> ResumeProfile。"""
from __future__ import annotations

import prompts
from schemas.resume import ResumeProfile
from services import llm_service


def run(resume_text: str) -> ResumeProfile:
    text = (resume_text or "").strip()
    if not text:
        return ResumeProfile()
    data = llm_service.chat_json(
        prompts.RESUME_SYSTEM,
        prompts.RESUME_USER.format(resume_text=text[:8000]),
    )
    return ResumeProfile.model_validate(data)
