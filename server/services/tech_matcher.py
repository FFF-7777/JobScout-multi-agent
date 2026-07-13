"""规则化技术证据归类。"""
from __future__ import annotations

from schemas.job import JobProfile
from schemas.match import SkillEvidenceItem, SkillEvidenceSummary
from schemas.resume import ResumeProfile


def _normalize(text: str) -> str:
    return (
        text.strip()
        .lower()
        .replace(" ", "")
        .replace(".", "")
        .replace("-", "")
        .replace("_", "")
    )


_ALIASES: dict[str, set[str]] = {
    "llamaindex": {"llamaindex", "llama_index", "llama index"},
    "langchain": {"langchain", "lang chain"},
    "langgraph": {"langgraph", "lang graph"},
    "openai": {"openai", "openai sdk", "openaiapi"},
    "rag": {"rag", "retrievalaugmentedgeneration", "检索增强生成"},
    "agent": {"agent", "aiagent", "智能体"},
    "vllm": {"vllm"},
    "sglang": {"sglang"},
    "pytorch": {"pytorch", "torch"},
    "transformers": {"transformers", "huggingface", "hftransformers"},
    "mysql": {"mysql"},
    "postgresql": {"postgresql", "postgres", "pgsql"},
    "fastapi": {"fastapi"},
    "django": {"django"},
    "flask": {"flask"},
    "python": {"python"},
    "javascript": {"javascript", "js"},
    "typescript": {"typescript", "ts"},
}

_FAMILIES: tuple[set[str], ...] = (
    {"python", "django", "flask", "fastapi"},
    {"langchain", "langgraph", "llamaindex", "rag", "agent"},
    {"pytorch", "transformers", "vllm", "sglang"},
    {"mysql", "postgresql", "sqlite", "redis"},
    {"javascript", "typescript", "react", "vue", "nodejs"},
)


def canonicalize(text: str) -> str:
    normalized = _normalize(text)
    if not normalized:
        return ""
    for canonical, aliases in _ALIASES.items():
        if normalized == canonical or normalized in {_normalize(x) for x in aliases}:
            return canonical
    return normalized


def _same_family(a: str, b: str) -> bool:
    if not a or not b:
        return False
    for family in _FAMILIES:
        if a in family and b in family:
            return True
    return False


def _resume_evidence_pool(resume: ResumeProfile) -> list[tuple[str, str]]:
    pool: list[tuple[str, str]] = []
    for skill in resume.skills:
        pool.append((canonicalize(skill), f"技能：{skill}"))
    for project in resume.projects:
        for keyword in project.keywords:
            label = f"项目《{project.name or '未命名项目'}》关键词：{keyword}"
            pool.append((canonicalize(keyword), label))
        if project.description:
            pool.append((canonicalize(project.description), f"项目《{project.name or '未命名项目'}》描述"))
    for role in resume.target_roles:
        pool.append((canonicalize(role), f"目标岗位：{role}"))
    return [(norm, evidence) for norm, evidence in pool if norm]


def _classify_one(requirement: str, source: str, pool: list[tuple[str, str]]) -> SkillEvidenceItem:
    canonical_req = canonicalize(requirement)
    requirement_text = requirement or canonical_req
    for norm, evidence in pool:
        if norm == canonical_req:
            return SkillEvidenceItem(
                skill=requirement_text,
                source=source,
                bucket="confirmed",
                job_requirement=requirement_text,
                resume_evidence=evidence,
                note="简历中有直接同名证据。",
            )
    for norm, evidence in pool:
        if canonical_req and norm and (canonical_req in norm or norm in canonical_req):
            return SkillEvidenceItem(
                skill=requirement_text,
                source=source,
                bucket="confirmed",
                job_requirement=requirement_text,
                resume_evidence=evidence,
                note="简历中存在高度相近的同类技术表述。",
            )
    for norm, evidence in pool:
        if _same_family(canonical_req, norm):
            return SkillEvidenceItem(
                skill=requirement_text,
                source=source,
                bucket="partial" if source == "required" else "transferable",
                job_requirement=requirement_text,
                resume_evidence=evidence,
                note="简历里有同技术家族经验，但不是岗位点名要求的同一技术。",
            )
    return SkillEvidenceItem(
        skill=requirement_text,
        source=source,
        bucket="not_shown",
        job_requirement=requirement_text,
        resume_evidence="当前简历未提供相关直接证据",
        note="当前材料里没有识别到可验证证据。",
    )


def build_skill_evidence(
    resume: ResumeProfile,
    job: JobProfile,
) -> tuple[list[SkillEvidenceItem], SkillEvidenceSummary]:
    pool = _resume_evidence_pool(resume)
    items: list[SkillEvidenceItem] = []
    for skill in job.required_skills or []:
        items.append(_classify_one(skill, "required", pool))
    for skill in job.preferred_skills or []:
        items.append(_classify_one(skill, "preferred", pool))

    summary = SkillEvidenceSummary(
        required_total=len(job.required_skills or []),
        preferred_total=len(job.preferred_skills or []),
        confirmed_count=sum(1 for item in items if item.bucket == "confirmed"),
        partial_count=sum(1 for item in items if item.bucket == "partial"),
        transferable_count=sum(1 for item in items if item.bucket == "transferable"),
        not_shown_count=sum(1 for item in items if item.bucket == "not_shown"),
    )
    return items, summary


def coverage_score(items: list[SkillEvidenceItem]) -> float:
    required = [item for item in items if item.source == "required"]
    if not required:
        return 60.0
    total = 0.0
    for item in required:
        total += {
            "confirmed": 1.0,
            "partial": 0.6,
            "transferable": 0.35,
            "not_shown": 0.0,
        }.get(item.bucket, 0.0)
    return round(total / len(required) * 100, 1)
