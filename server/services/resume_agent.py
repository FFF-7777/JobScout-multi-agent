"""Resume Agent：解析简历文本 -> ResumeProfile。"""
from __future__ import annotations

import prompts
from schemas.resume import ResumeProfile
from services import llm_service


def compute_hash(text: str) -> str:
    """简历内容哈希（用于缓存：内容不变则跳过重新解析）。"""
    import hashlib

    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def run(resume_text: str, *, model_role: str = "fast") -> ResumeProfile:
    text = (resume_text or "").strip()
    if not text:
        return ResumeProfile()
    data = llm_service.chat_json(
        prompts.RESUME_SYSTEM,
        prompts.RESUME_USER.format(resume_text=text[:8000]),
        model_role=model_role,
    )
    return ResumeProfile.model_validate(data)
