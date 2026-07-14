#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
JobScout 零人工数据自动量化评估

作用：
1. 自动读取 server/sample_data/sample_resume.md 与 sample_jobs.csv
2. 自动生成噪声、重复文本、硬条件冲突和三类技能测试：
   - 与岗位职责直接相关的核心技能
   - 非硬性的加分技能
   - 与岗位无关的技术关键词污染
3. 调用正在运行的 JobScout 后端完成岗位解析与匹配
4. 自动计算：
   - 工作流成功率
   - 结构化输出完整度
   - 证据可追溯率（启发式代理指标）
   - 噪声鲁棒性
   - 重复文本鲁棒性
   - 硬条件敏感性
   - 相关核心技能敏感性
   - 加分技能校准能力
   - 无关技能污染鲁棒性
   - 正负岗位区分度
5. 输出 JSON、CSV、Markdown 报告

运行前：
    cd server
    .\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8020

另开一个 PowerShell：
    cd server
    .\.venv\Scripts\python.exe auto_eval.py

可选参数：
    .\.venv\Scripts\python.exe auto_eval.py --max-source-jobs 3
    .\.venv\Scripts\python.exe auto_eval.py --base-url http://127.0.0.1:8020
    .\.venv\Scripts\python.exe auto_eval.py --timeout 900

注意：
这是一套“无人工黄金集的自动代理评估”，适合快速回归、模型对比和发现明显问题。
它不能替代人工标注的真实岗位排序准确率、HR 认可率或真实投递成功率。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import httpx


ACTION_RANK = {
    "skip": 0,
    "selective_apply": 1,
    "apply": 2,
    "priority_apply": 3,
}

SKILL_CANDIDATES = [
    "Python", "FastAPI", "Flask", "Django", "LangChain", "LangGraph",
    "RAG", "Agent", "Multi-Agent", "Tool Calling", "Function Calling",
    "LLM API", "MCP", "Prompt Engineering", "结构化输出", "Agent 工作流",
    "ChromaDB", "FAISS", "Milvus", "向量数据库", "BM25", "RRF",
    "Embedding", "Rerank", "评估框架", "PyTorch", "TensorFlow",
    "Transformer", "SQL", "MySQL", "PostgreSQL", "Redis", "Docker",
    "Kubernetes", "Linux", "Git", "Java", "Spring Boot", "Go", "Rust",
    "C++", "CUDA", "Spark", "Flink", "Kafka", "React", "Vue",
    "TypeScript", "JavaScript", "NLP", "OCR", "OpenCV", "云部署",
]

# 按岗位方向生成“相关核心技能”和“非硬性加分技能”，避免把完全无关的
# FPGA、射频等技术错误地当成所有岗位的核心要求。
ROLE_SKILL_PROFILES: dict[str, dict[str, list[str]]] = {
    "ai_agent": {
        "core": [
            "LangGraph", "Tool Calling", "RAG", "LLM API",
            "结构化输出", "Agent 工作流",
        ],
        "preferred": [
            "Docker", "Redis", "向量数据库", "评估框架", "MCP", "云部署",
        ],
    },
    "backend": {
        "core": ["Python", "FastAPI", "SQL", "REST API", "数据库设计"],
        "preferred": ["Redis", "Docker", "消息队列", "Linux", "云部署"],
    },
    "frontend": {
        "core": ["TypeScript", "Vue", "React", "JavaScript", "前端工程化"],
        "preferred": ["Vite", "状态管理", "单元测试", "Docker", "性能优化"],
    },
    "data": {
        "core": ["Python", "SQL", "数据清洗", "数据分析", "统计建模"],
        "preferred": ["Spark", "Flink", "Kafka", "可视化", "云部署"],
    },
    "cv": {
        "core": ["Python", "PyTorch", "OpenCV", "计算机视觉", "模型训练"],
        "preferred": ["CUDA", "Docker", "模型部署", "数据标注", "性能优化"],
    },
    "general": {
        "core": ["Python", "Git", "接口开发", "问题分析"],
        "preferred": ["Docker", "Linux", "SQL", "单元测试", "云部署"],
    },
}

IRRELEVANT_SKILL_POOL = [
    "SAP ABAP", "嵌入式 Linux 驱动", "FPGA Verilog", "射频电路设计",
    "CATIA", "AutoCAD 二次开发", "Oracle DBA", "COBOL",
    "PLC 梯形图", "机械结构设计",
]


@dataclass
class EvalCase:
    case_id: str
    family: str
    source_index: int
    jd_text: str
    expected: str
    job_id: int | None = None


@dataclass
class ResultRow:
    case_id: str
    family: str
    source_index: int
    job_id: int | None
    status: str
    score: float | None
    level: str
    action: str
    confidence: float | None
    schema_completeness: float
    grounded_items: int
    evidence_items: int
    grounding_rate: float | None
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JobScout 零人工数据自动量化评估")
    parser.add_argument("--base-url", default="http://127.0.0.1:8020")
    parser.add_argument("--max-source-jobs", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=900, help="单个工作流最长等待秒数")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--noise-delta", type=float, default=8.0)
    parser.add_argument("--hard-drop", type=float, default=5.0)
    parser.add_argument(
        "--core-skill-drop",
        type=float,
        default=5.0,
        help="加入相关核心技能要求后，期望的最小降分",
    )
    parser.add_argument(
        "--preferred-delta",
        type=float,
        default=6.0,
        help="加入非硬性加分技能后允许的最大绝对分差",
    )
    parser.add_argument(
        "--irrelevant-delta",
        type=float,
        default=8.0,
        help="混入无关技术关键词后允许的最大绝对分差",
    )
    parser.add_argument("--anchor-gap", type=float, default=20.0)
    parser.add_argument("--output-dir", default="eval_outputs")
    return parser.parse_args()


def resolve_paths(output_dir_name: str) -> tuple[Path, Path]:
    script_dir = Path(__file__).resolve().parent
    project_dir = script_dir.parent
    server_dir = project_dir / "server"
    return server_dir, script_dir / output_dir_name


def compact_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_text(value: Any) -> str:
    text = compact_text(value).lower()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def char_ngrams(text: str, n: int = 2) -> set[str]:
    text = normalize_text(text)
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i+n] for i in range(len(text) - n + 1)}


def evidence_is_grounded(evidence: str, source: str) -> bool:
    """
    纯本地启发式证据核验：
    - 完整子串命中直接通过
    - 否则使用中文/英文字符二元组覆盖率
    这是代理指标，不等同于语义蕴含模型。
    """
    ev = normalize_text(evidence)
    src = normalize_text(source)
    if not ev:
        return False
    if ev in src:
        return True
    grams = char_ngrams(ev, 2)
    src_grams = char_ngrams(src, 2)
    if not grams:
        return False
    coverage = len(grams & src_grams) / len(grams)
    return coverage >= 0.48


def pick_column(fieldnames: Iterable[str], candidates: list[str]) -> str | None:
    normalized = {normalize_text(name): name for name in fieldnames if name}
    for candidate in candidates:
        key = normalize_text(candidate)
        if key in normalized:
            return normalized[key]
    return None


def load_source_data(server_dir: Path, max_jobs: int) -> tuple[str, list[str]]:
    sample_dir = server_dir / "sample_data"
    resume_path = sample_dir / "sample_resume.md"
    jobs_path = sample_dir / "sample_jobs.csv"

    if not resume_path.exists():
        raise FileNotFoundError(f"找不到示例简历：{resume_path}")
    if not jobs_path.exists():
        raise FileNotFoundError(f"找不到示例岗位：{jobs_path}")

    resume_text = resume_path.read_text(encoding="utf-8-sig").strip()
    if not resume_text:
        raise ValueError("sample_resume.md 为空")

    with jobs_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        if not rows:
            raise ValueError("sample_jobs.csv 没有岗位数据")

        fieldnames = reader.fieldnames or []
        jd_column = pick_column(
            fieldnames,
            [
                "jd_text", "jd", "职位描述", "岗位描述", "工作内容",
                "description", "job_description", "content",
            ],
        )

        jobs: list[str] = []
        for row in rows:
            if jd_column:
                jd = compact_text(row.get(jd_column))
            else:
                # 未找到标准列时，把整行非空字段拼成可解析岗位文本。
                jd = "\n".join(
                    f"{key}: {compact_text(value)}"
                    for key, value in row.items()
                    if compact_text(value)
                )
            if len(jd) >= 30:
                jobs.append(jd)
            if len(jobs) >= max_jobs:
                break

    if not jobs:
        raise ValueError("没有从 sample_jobs.csv 提取到有效 JD")
    return resume_text, jobs


def contains_skill(text: str, skill: str) -> bool:
    """判断文本是否包含技能，兼容大小写、空格和连字符差异。"""
    return normalize_text(skill) in normalize_text(text)


def extract_known_skills(text: str) -> list[str]:
    return [skill for skill in SKILL_CANDIDATES if contains_skill(text, skill)]


def infer_role_group(jd_text: str) -> str:
    normalized = normalize_text(jd_text)
    keyword_groups = [
        (
            "ai_agent",
            [
                "agent", "rag", "大模型", "llm", "langchain", "langgraph",
                "prompt", "智能体", "模型应用", "向量数据库",
            ],
        ),
        (
            "cv",
            [
                "计算机视觉", "opencv", "目标检测", "图像识别", "图像算法",
                "视觉算法", "ocr", "yolo",
            ],
        ),
        (
            "data",
            [
                "数据分析", "数据开发", "数据工程", "数仓", "spark", "flink",
                "kafka", "统计建模", "商业分析",
            ],
        ),
        (
            "frontend",
            [
                "前端", "vue", "react", "typescript", "javascript",
                "小程序", "网页开发",
            ],
        ),
        (
            "backend",
            [
                "后端", "服务端", "fastapi", "django", "flask", "spring",
                "接口开发", "微服务", "数据库",
            ],
        ),
    ]
    for role_group, keywords in keyword_groups:
        if any(normalize_text(keyword) in normalized for keyword in keywords):
            return role_group
    return "general"


def unique_absent_skills(
    candidates: Iterable[str],
    resume_text: str,
    excluded: Iterable[str] = (),
    limit: int = 2,
) -> list[str]:
    excluded_normalized = {normalize_text(item) for item in excluded}
    selected: list[str] = []
    seen: set[str] = set()
    for skill in candidates:
        key = normalize_text(skill)
        if (
            not key
            or key in seen
            or key in excluded_normalized
            or contains_skill(resume_text, skill)
        ):
            continue
        selected.append(skill)
        seen.add(key)
        if len(selected) >= limit:
            break
    return selected


def choose_skill_tests(
    resume_text: str,
    jd_text: str,
) -> tuple[list[str], list[str], list[str], str]:
    """
    自动构造三类技能测试。

    1. 相关核心技能：优先选 JD 已出现、但简历未出现的技能；不足时从相同
       岗位方向的核心技能池补充。
    2. 加分技能：从相同岗位方向的辅助技能池选择，并明确标注为非硬性。
    3. 无关技能：从其他行业技术池选择，用于测试技术关键词污染鲁棒性。
    """
    role_group = infer_role_group(jd_text)
    profile = ROLE_SKILL_PROFILES[role_group]
    jd_skills = extract_known_skills(jd_text)

    relevant_missing_in_jd = unique_absent_skills(
        jd_skills,
        resume_text,
        limit=2,
    )
    core_skills = relevant_missing_in_jd
    if len(core_skills) < 2:
        core_skills.extend(
            unique_absent_skills(
                profile["core"],
                resume_text,
                excluded=core_skills,
                limit=2 - len(core_skills),
            )
        )

    preferred_skills = unique_absent_skills(
        profile["preferred"],
        resume_text,
        excluded=core_skills,
        limit=2,
    )

    irrelevant_skills = unique_absent_skills(
        IRRELEVANT_SKILL_POOL,
        resume_text + "\n" + jd_text,
        excluded=core_skills + preferred_skills,
        limit=3,
    )

    # 极端情况下仍保证每类测试有内容。
    if not core_skills:
        core_skills = ["岗位方向对应的核心工具实践"]
    if not preferred_skills:
        preferred_skills = ["相关工程化工具"]
    if not irrelevant_skills:
        irrelevant_skills = IRRELEVANT_SKILL_POOL[:3]

    return core_skills, preferred_skills, irrelevant_skills, role_group


def extract_resume_skills(resume_text: str) -> tuple[list[str], list[str]]:
    present = extract_known_skills(resume_text)
    absent = [
        skill
        for skill in IRRELEVANT_SKILL_POOL
        if not contains_skill(resume_text, skill)
    ]
    if not present:
        # 兜底：抽取英文技术词，避免锚点完全空白。
        tokens = re.findall(r"\b[A-Za-z][A-Za-z0-9.+#/-]{1,24}\b", resume_text)
        ignored = {"and", "with", "from", "the", "for", "using", "project"}
        present = []
        for token in tokens:
            if token.lower() not in ignored and token not in present:
                present.append(token)
            if len(present) >= 6:
                break
    return present[:8], absent[:6]


def build_cases(resume_text: str, source_jobs: list[str]) -> list[EvalCase]:
    present_skills, absent_skills = extract_resume_skills(resume_text)
    cases: list[EvalCase] = []

    for index, jd in enumerate(source_jobs):
        core_skills, preferred_skills, irrelevant_skills, role_group = (
            choose_skill_tests(resume_text, jd)
        )
        core_text = "、".join(core_skills)
        preferred_text = "、".join(preferred_skills)
        irrelevant_text = "、".join(irrelevant_skills)

        cases.extend(
            [
                EvalCase(
                    case_id=f"source_{index}_baseline",
                    family="baseline",
                    source_index=index,
                    jd_text=jd,
                    expected="原始基线",
                ),
                EvalCase(
                    case_id=f"source_{index}_noise",
                    family="noise",
                    source_index=index,
                    jd_text=(
                        jd
                        + "\n\n页面无关信息：收藏、举报、微信扫码分享、公司环境照片、"
                          "相关推荐、登录后沟通、免责声明。"
                    ),
                    expected="加入网页噪声后，分数和投递决策应基本稳定",
                ),
                EvalCase(
                    case_id=f"source_{index}_duplicate",
                    family="duplicate",
                    source_index=index,
                    jd_text=(
                        jd
                        + "\n\n以下是招聘页面重复展示的内容，不代表新增要求：\n"
                        + jd[: min(500, len(jd))]
                    ),
                    expected="重复内容不应明显抬高或压低匹配分数",
                ),
                EvalCase(
                    case_id=f"source_{index}_hard_fail",
                    family="hard_fail",
                    source_index=index,
                    jd_text=(
                        jd
                        + "\n\n新增硬性要求：必须具有博士学历；"
                          "必须具有 5 年以上同岗位全职工作经验；"
                          "每周必须到岗 7 天；任一条件不满足均不录用。"
                    ),
                    expected="加入明显不满足的一票否决条件后，分数或投递决策应下降",
                ),
                EvalCase(
                    case_id=f"source_{index}_relevant_core_skill",
                    family="relevant_core_skill",
                    source_index=index,
                    jd_text=(
                        jd
                        + f"\n\n新增与本岗位主要职责直接相关的核心要求（岗位方向：{role_group}）："
                          f"需要在实际项目中独立使用 {core_text} 完成核心工作；"
                          "不接受仅了解概念或完全没有相关实践。"
                    ),
                    expected=(
                        f"加入与岗位职责相关、且简历未体现的核心技能（{core_text}）后，"
                        "分数或投递优先级应合理下降"
                    ),
                ),
                EvalCase(
                    case_id=f"source_{index}_preferred_skill",
                    family="preferred_skill",
                    source_index=index,
                    jd_text=(
                        jd
                        + f"\n\n非硬性加分项：了解或使用过 {preferred_text} 者优先；"
                          "没有这些技能不影响正常投递，也不作为淘汰条件。"
                    ),
                    expected=(
                        f"加入非硬性加分技能（{preferred_text}）后，不应因缺失而明显降分"
                    ),
                ),
                EvalCase(
                    case_id=f"source_{index}_irrelevant_skill",
                    family="irrelevant_skill",
                    source_index=index,
                    jd_text=(
                        jd
                        + f"\n\n招聘页面底部混入其他业务线的历史模板关键词："
                          f"{irrelevant_text}。这些词未出现在本岗位职责或任职要求中。"
                    ),
                    expected=(
                        f"混入与岗位无关的技术关键词（{irrelevant_text}）后，"
                        "分数和投递决策应基本稳定"
                    ),
                ),
            ]
        )

    positive_skills = "、".join(present_skills or ["Python", "Agent", "RAG"])
    negative_skills = "、".join(
        absent_skills or IRRELEVANT_SKILL_POOL[:5]
    )

    cases.extend(
        [
            EvalCase(
                case_id="anchor_positive",
                family="anchor_positive",
                source_index=-1,
                jd_text=f"""
公司：自动评估正向锚点公司
岗位：AI Agent 应用开发实习生
工作地点：远程或可协商
学历：本科在读
经验：接受在校项目经验
岗位职责：
1. 参与 AI Agent、RAG 和大模型应用开发；
2. 使用 Python 完成功能开发、接口联调和效果优化；
3. 能够清晰说明自己的项目设计、技术取舍和实验结果。
核心要求：
1. 掌握或有项目实践：{positive_skills}；
2. 接受优秀在校生和个人项目经验；
3. 无全职工作年限硬性要求。
""".strip(),
                expected="根据示例简历自动生成的高匹配岗位，得分应明显高于负向锚点",
            ),
            EvalCase(
                case_id="anchor_negative",
                family="anchor_negative",
                source_index=-1,
                jd_text=f"""
公司：自动评估负向锚点公司
岗位：高级硬件与底层系统专家
工作地点：必须异地驻场
学历：博士
经验：8 年以上同岗位全职经验
核心职责：
1. 独立负责芯片、射频、FPGA 和底层驱动设计；
2. 承担生产环境重大故障责任；
3. 管理十人以上硬件研发团队。
核心要求，缺一不可：
1. {negative_skills}；
2. 博士学历；
3. 8 年以上全职经验；
4. 不接受实习生、应届生或项目经验替代。
""".strip(),
                expected="明显不匹配岗位，得分和投递决策应低于正向锚点",
            ),
        ]
    )
    return cases


def request_json(
    client: httpx.Client,
    method: str,
    path: str,
    **kwargs: Any,
) -> Any:
    response = client.request(method, path, **kwargs)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = response.text[:1500]
        raise RuntimeError(f"{method} {path} 失败：{response.status_code}\n{body}") from exc
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"{method} {path} 未返回 JSON：{response.text[:1000]}") from exc


def extract_created_job(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list) and payload:
        return payload[0]
    if isinstance(payload, dict):
        if "id" in payload:
            return payload
        created = payload.get("created")
        if isinstance(created, list) and created:
            return created[0]
        items = payload.get("items")
        if isinstance(items, list) and items:
            return items[0]
    raise RuntimeError(f"无法从岗位导入响应中获得 job id：{str(payload)[:1000]}")


def import_resume(client: httpx.Client, resume_text: str) -> int:
    payload = request_json(
        client,
        "POST",
        "/api/resumes/parse",
        json={"text": resume_text, "filename": "auto_eval_sample_resume.md"},
    )
    resume_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(resume_id, int):
        raise RuntimeError(f"简历解析响应缺少 id：{payload}")
    return resume_id


def import_cases(client: httpx.Client, cases: list[EvalCase]) -> None:
    for number, case in enumerate(cases, start=1):
        payload = request_json(
            client,
            "POST",
            "/api/jobs/import-text",
            json={"jd_text": case.jd_text, "split_batch": False},
        )
        job = extract_created_job(payload)
        job_id = job.get("id")
        if not isinstance(job_id, int):
            raise RuntimeError(f"{case.case_id} 的岗位导入响应缺少 id：{job}")
        case.job_id = job_id
        print(f"[导入 {number:02d}/{len(cases):02d}] {case.case_id} -> job_id={job_id}")


def run_workflow(
    client: httpx.Client,
    resume_id: int,
    job_ids: list[int],
    timeout_seconds: int,
    poll_interval: float,
) -> tuple[str, float, dict[str, Any]]:
    started = time.perf_counter()
    payload = request_json(
        client,
        "POST",
        "/api/agents/run",
        json={"resume_id": resume_id, "job_ids": job_ids},
    )
    task_id = payload.get("task_id")
    if not task_id:
        raise RuntimeError(f"工作流响应缺少 task_id：{payload}")

    last_status = ""
    last_progress = ""
    deadline = time.monotonic() + timeout_seconds
    final_payload: dict[str, Any] = payload

    while time.monotonic() < deadline:
        final_payload = request_json(client, "GET", f"/api/agents/tasks/{task_id}")
        status = str(final_payload.get("status", ""))
        steps = final_payload.get("steps") or []
        progress = " | ".join(
            f"{step.get('agent_name')}:{step.get('status')}({step.get('progress', 0)}%)"
            for step in steps
        )
        if status != last_status or progress != last_progress:
            print(f"[工作流] {status} | {progress}")
            last_status = status
            last_progress = progress

        if status in {"completed", "failed", "completed_with_errors"}:
            elapsed = time.perf_counter() - started
            return str(task_id), elapsed, final_payload
        time.sleep(poll_interval)

    raise TimeoutError(f"任务 {task_id} 超过 {timeout_seconds} 秒仍未完成")


def fetch_results(client: httpx.Client, task_id: str) -> list[dict[str, Any]]:
    payload = request_json(
        client,
        "GET",
        "/api/match/results",
        params={"task_id": task_id, "page_size": 200},
    )
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise RuntimeError(f"无法识别匹配结果响应：{str(payload)[:1500]}")


def get_detail(result: dict[str, Any]) -> dict[str, Any]:
    detail = result.get("detail_json")
    return detail if isinstance(detail, dict) else {}


def get_action(result: dict[str, Any]) -> str:
    detail = get_detail(result)
    decision = detail.get("application_decision")
    if isinstance(decision, dict):
        return compact_text(decision.get("action")) or "unknown"
    return "unknown"


def get_confidence(result: dict[str, Any]) -> float | None:
    value = get_detail(result).get("confidence")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def schema_completeness(result: dict[str, Any]) -> float:
    detail = get_detail(result)
    required_detail_keys = [
        "dimensions",
        "core_job_requirements",
        "hard_condition_result",
        "top_strengths",
        "main_gaps",
        "hr_screening",
        "career_alignment",
        "application_decision",
        "next_actions",
        "confidence",
    ]
    checks = [
        result.get("score") is not None,
        bool(compact_text(result.get("level"))),
        bool(compact_text(result.get("recommendation"))),
        *[key in detail and detail.get(key) is not None for key in required_detail_keys],
    ]
    return sum(bool(item) for item in checks) / len(checks)


def collect_evidence(
    result: dict[str, Any],
    resume_text: str,
    jd_text: str,
) -> list[tuple[str, str]]:
    """返回 (证据文本, 应核对来源)。"""
    pairs: list[tuple[str, str]] = []
    detail = get_detail(result)

    for item in result.get("matched_points") or []:
        if compact_text(item):
            pairs.append((compact_text(item), resume_text + "\n" + jd_text))

    strengths = detail.get("top_strengths")
    if isinstance(strengths, list):
        for item in strengths:
            if isinstance(item, dict):
                resume_evidence = compact_text(item.get("resume_evidence"))
                job_relevance = compact_text(item.get("job_relevance"))
                if resume_evidence:
                    pairs.append((resume_evidence, resume_text))
                if job_relevance:
                    pairs.append((job_relevance, jd_text))

    hard = detail.get("hard_condition_result")
    if isinstance(hard, dict):
        items = hard.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                resume_evidence = compact_text(item.get("resume_evidence"))
                job_requirement = compact_text(item.get("job_requirement"))
                if resume_evidence and resume_evidence not in {"未提及", "无", "未知"}:
                    pairs.append((resume_evidence, resume_text))
                if job_requirement:
                    pairs.append((job_requirement, jd_text))

    requirements = detail.get("core_job_requirements")
    if isinstance(requirements, list):
        for item in requirements:
            if compact_text(item):
                pairs.append((compact_text(item), jd_text))

    return pairs


def build_result_rows(
    cases: list[EvalCase],
    raw_results: list[dict[str, Any]],
    resume_text: str,
) -> tuple[list[ResultRow], dict[int, dict[str, Any]]]:
    by_job_id = {
        int(item["job_id"]): item
        for item in raw_results
        if isinstance(item, dict) and isinstance(item.get("job_id"), int)
    }
    rows: list[ResultRow] = []

    for case in cases:
        result = by_job_id.get(case.job_id or -1)
        if not result:
            rows.append(
                ResultRow(
                    case_id=case.case_id,
                    family=case.family,
                    source_index=case.source_index,
                    job_id=case.job_id,
                    status="missing",
                    score=None,
                    level="",
                    action="unknown",
                    confidence=None,
                    schema_completeness=0.0,
                    grounded_items=0,
                    evidence_items=0,
                    grounding_rate=None,
                    error="未找到匹配结果",
                )
            )
            continue

        status = compact_text(result.get("status")) or "success"
        try:
            score = float(result.get("score"))
        except (TypeError, ValueError):
            score = None

        evidence_pairs = collect_evidence(result, resume_text, case.jd_text)
        grounded_count = sum(
            evidence_is_grounded(evidence, source)
            for evidence, source in evidence_pairs
        )
        grounding_rate = (
            grounded_count / len(evidence_pairs) if evidence_pairs else None
        )

        rows.append(
            ResultRow(
                case_id=case.case_id,
                family=case.family,
                source_index=case.source_index,
                job_id=case.job_id,
                status=status,
                score=score,
                level=compact_text(result.get("level")),
                action=get_action(result),
                confidence=get_confidence(result),
                schema_completeness=schema_completeness(result),
                grounded_items=grounded_count,
                evidence_items=len(evidence_pairs),
                grounding_rate=grounding_rate,
                error=compact_text(result.get("error_message")),
            )
        )
    return rows, by_job_id


def mean_or_zero(values: Iterable[float]) -> float:
    values = list(values)
    return statistics.fmean(values) if values else 0.0


def pct(value: float) -> float:
    return round(max(0.0, min(1.0, value)) * 100, 2)


def evaluate_metrics(
    rows: list[ResultRow],
    args: argparse.Namespace,
    elapsed: float,
    final_task: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    row_by_id = {row.case_id: row for row in rows}
    successful = [
        row for row in rows
        if row.status == "success" and row.score is not None
    ]

    success_rate = len(successful) / len(rows) if rows else 0.0
    structure_rate = mean_or_zero(row.schema_completeness for row in successful)
    grounding_values = [
        row.grounding_rate
        for row in successful
        if row.grounding_rate is not None
    ]
    grounding_rate = mean_or_zero(grounding_values)

    comparisons: list[dict[str, Any]] = []
    compared_families = (
        "noise",
        "duplicate",
        "hard_fail",
        "relevant_core_skill",
        "preferred_skill",
        "irrelevant_skill",
    )
    family_passes: dict[str, list[bool]] = {
        family: [] for family in compared_families
    }

    source_indices = sorted({
        row.source_index for row in rows if row.source_index >= 0
    })

    for index in source_indices:
        base = row_by_id.get(f"source_{index}_baseline")
        if not base or base.score is None:
            continue

        for family in compared_families:
            variant = row_by_id.get(f"source_{index}_{family}")
            if not variant or variant.score is None:
                family_passes[family].append(False)
                comparisons.append({
                    "source_index": index,
                    "family": family,
                    "pass": False,
                    "reason": "缺少结果",
                })
                continue

            delta = variant.score - base.score
            action_delta = (
                ACTION_RANK.get(variant.action, -1)
                - ACTION_RANK.get(base.action, -1)
            )
            base_is_non_skip = base.action != "skip"

            if family == "noise":
                passed = (
                    abs(delta) <= args.noise_delta
                    and abs(action_delta) <= 1
                    and not (
                        base_is_non_skip
                        and variant.action == "skip"
                    )
                )
                rule = (
                    f"|分差|≤{args.noise_delta}，投递动作最多变化一级，"
                    "不得从可投直接变为 skip"
                )
            elif family == "duplicate":
                passed = (
                    abs(delta) <= args.noise_delta
                    and abs(action_delta) <= 1
                    and not (
                        base_is_non_skip
                        and variant.action == "skip"
                    )
                )
                rule = (
                    f"|分差|≤{args.noise_delta}，重复文本不得明显改变投递判断"
                )
            elif family == "hard_fail":
                passed = (
                    (delta <= -args.hard_drop or action_delta < 0)
                    and delta <= 3
                    and action_delta <= 0
                )
                rule = (
                    f"至少降分 {args.hard_drop}，或投递动作降级；"
                    "不得升分或升级"
                )
            elif family == "relevant_core_skill":
                passed = (
                    (
                        delta <= -args.core_skill_drop
                        or action_delta < 0
                    )
                    and delta <= 3
                    and action_delta <= 0
                )
                rule = (
                    f"相关核心技能缺失应至少降分 {args.core_skill_drop}，"
                    "或使投递动作降级；不得升级"
                )
            elif family == "preferred_skill":
                passed = (
                    -args.preferred_delta <= delta <= 3
                    and -1 <= action_delta <= 0
                    and not (
                        base_is_non_skip
                        and variant.action == "skip"
                    )
                )
                rule = (
                    f"非硬性加分项缺失最多降 {args.preferred_delta} 分，"
                    "动作最多降一级且不得直接 skip"
                )
            else:  # irrelevant_skill
                passed = (
                    abs(delta) <= args.irrelevant_delta
                    and abs(action_delta) <= 1
                    and not (
                        base_is_non_skip
                        and variant.action == "skip"
                    )
                )
                rule = (
                    f"|分差|≤{args.irrelevant_delta}，无关技术关键词不得"
                    "明显改变投递判断"
                )

            family_passes[family].append(passed)
            comparisons.append({
                "source_index": index,
                "family": family,
                "baseline_score": round(base.score, 2),
                "variant_score": round(variant.score, 2),
                "score_delta": round(delta, 2),
                "baseline_action": base.action,
                "variant_action": variant.action,
                "action_delta": action_delta,
                "rule": rule,
                "pass": passed,
            })

    positive = row_by_id.get("anchor_positive")
    negative = row_by_id.get("anchor_negative")
    anchor_pass = False
    anchor_gap = None
    if (
        positive and negative
        and positive.score is not None
        and negative.score is not None
    ):
        anchor_gap = positive.score - negative.score
        anchor_pass = (
            anchor_gap >= args.anchor_gap
            and ACTION_RANK.get(positive.action, -1)
            > ACTION_RANK.get(negative.action, -1)
        )

    def pass_rate(name: str) -> float:
        values = family_passes[name]
        return sum(values) / len(values) if values else 0.0

    noise_rate = pass_rate("noise")
    duplicate_rate = pass_rate("duplicate")
    hard_rate = pass_rate("hard_fail")
    core_skill_rate = pass_rate("relevant_core_skill")
    preferred_skill_rate = pass_rate("preferred_skill")
    irrelevant_skill_rate = pass_rate("irrelevant_skill")
    anchor_rate = 1.0 if anchor_pass else 0.0

    # 总权重为 100%。三类技能测试被拆开，避免把无关技术栈缺失
    # 错误地当作“核心技能缺失”扣分。
    weights = {
        "workflow_success_rate": 0.15,
        "schema_completeness": 0.10,
        "evidence_grounding_proxy": 0.20,
        "noise_robustness": 0.10,
        "duplicate_robustness": 0.05,
        "hard_condition_sensitivity": 0.15,
        "relevant_core_skill_sensitivity": 0.10,
        "preferred_skill_calibration": 0.05,
        "irrelevant_skill_robustness": 0.05,
        "anchor_separation": 0.05,
    }
    raw_metric_values = {
        "workflow_success_rate": success_rate,
        "schema_completeness": structure_rate,
        "evidence_grounding_proxy": grounding_rate,
        "noise_robustness": noise_rate,
        "duplicate_robustness": duplicate_rate,
        "hard_condition_sensitivity": hard_rate,
        "relevant_core_skill_sensitivity": core_skill_rate,
        "preferred_skill_calibration": preferred_skill_rate,
        "irrelevant_skill_robustness": irrelevant_skill_rate,
        "anchor_separation": anchor_rate,
    }

    composite = sum(
        raw_metric_values[name] * weight
        for name, weight in weights.items()
    ) * 100

    if composite >= 90:
        grade = "A"
    elif composite >= 80:
        grade = "B"
    elif composite >= 70:
        grade = "C"
    elif composite >= 60:
        grade = "D"
    else:
        grade = "E"

    task_steps = final_task.get("steps") or []
    failed_steps = [
        {
            "agent_name": step.get("agent_name"),
            "error_message": step.get("error_message"),
        }
        for step in task_steps
        if step.get("status") == "failed"
    ]

    metrics = {
        "evaluation_type": "zero_manual_data_proxy_evaluation_v2",
        "generated_at": datetime.now().astimezone().isoformat(),
        "composite_score": round(composite, 2),
        "grade": grade,
        "case_count": len(rows),
        "successful_case_count": len(successful),
        "elapsed_seconds": round(elapsed, 2),
        "average_seconds_per_case": round(elapsed / len(rows), 2) if rows else None,
        "metrics": {
            key: {
                "value": pct(raw_metric_values[key]),
                "weight": int(weights[key] * 100),
            }
            for key in weights
        },
        "anchor": {
            "positive_score": positive.score if positive else None,
            "positive_action": positive.action if positive else None,
            "negative_score": negative.score if negative else None,
            "negative_action": negative.action if negative else None,
            "score_gap": round(anchor_gap, 2) if anchor_gap is not None else None,
            "pass": anchor_pass,
        },
        "failed_steps": failed_steps,
        "thresholds": {
            "noise_max_absolute_delta": args.noise_delta,
            "hard_condition_min_drop": args.hard_drop,
            "relevant_core_skill_min_drop": args.core_skill_drop,
            "preferred_skill_max_absolute_delta": args.preferred_delta,
            "irrelevant_skill_max_absolute_delta": args.irrelevant_delta,
            "anchor_min_gap": args.anchor_gap,
        },
        "limitations": [
            "没有人工黄金标签，因此不能计算真实岗位排序 NDCG、人工投递决策 F1 或 HR 认可率。",
            "证据可追溯率使用字符重叠启发式，只是幻觉风险代理，不是严格语义蕴含。",
            "相关核心技能由 JD 已出现技能和岗位方向技能池自动生成，仍属于合成测试。",
            "自动生成的正负样本适合回归测试和模型对比，不代表真实招聘市场分布。",
        ],
    }
    return metrics, comparisons


def write_outputs(
    output_dir: Path,
    metrics: dict[str, Any],
    rows: list[ResultRow],
    comparisons: list[dict[str, Any]],
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"auto_eval_v2_{stamp}.json"
    csv_path = output_dir / f"auto_eval_v2_cases_{stamp}.csv"
    md_path = output_dir / f"auto_eval_v2_{stamp}.md"

    json_path.write_text(
        json.dumps(
            {
                "summary": metrics,
                "cases": [asdict(row) for row in rows],
                "comparisons": comparisons,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        fieldnames = list(asdict(rows[0]).keys()) if rows else ["case_id"]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))

    metric_lines = []
    for name, value in metrics["metrics"].items():
        metric_lines.append(
            f"| {name} | {value['value']:.2f}% | {value['weight']}% |"
        )

    failed_case_lines = [
        f"- `{row.case_id}`：{row.error or row.status}"
        for row in rows
        if row.status != "success"
    ] or ["- 无"]

    comparison_lines = []
    for item in comparisons:
        symbol = "通过" if item.get("pass") else "失败"
        comparison_lines.append(
            f"| {item.get('source_index')} | {item.get('family')} | "
            f"{item.get('baseline_score', '-')} / "
            f"`{item.get('baseline_action', '-')}` | "
            f"{item.get('variant_score', '-')} / "
            f"`{item.get('variant_action', '-')}` | "
            f"{item.get('score_delta', '-')} | {symbol} |"
        )

    anchor = metrics["anchor"]
    md = f"""# JobScout 零人工数据自动量化评估 V2

- 综合代理得分：**{metrics['composite_score']} / 100**
- 等级：**{metrics['grade']}**
- 测试样本：{metrics['case_count']}
- 成功样本：{metrics['successful_case_count']}
- 总耗时：{metrics['elapsed_seconds']} 秒
- 平均每个样本：{metrics['average_seconds_per_case']} 秒

## 分项指标

| 指标 | 得分 | 权重 |
|---|---:|---:|
{chr(10).join(metric_lines)}

## 正负锚点

- 正向岗位：{anchor['positive_score']} 分，决策 `{anchor['positive_action']}`
- 负向岗位：{anchor['negative_score']} 分，决策 `{anchor['negative_action']}`
- 分差：{anchor['score_gap']}
- 是否通过：{'是' if anchor['pass'] else '否'}

## 变形测试明细

| 原始岗位序号 | 测试类型 | 基线分/决策 | 变形分/决策 | 分差 | 结果 |
|---:|---|---|---|---:|---|
{chr(10).join(comparison_lines)}

## 失败样本

{chr(10).join(failed_case_lines)}

## 如何理解结果

- `workflow_success_rate`：批量岗位是否都成功完成。
- `schema_completeness`：五维分数、硬条件、优势、缺口、HR 判断、投递决策等结构是否完整。
- `evidence_grounding_proxy`：输出证据能否在简历或 JD 原文中找到对应内容。
- `noise_robustness`：加入收藏、举报等页面噪声后结果是否稳定。
- `duplicate_robustness`：JD 内容重复后结果是否被重复信息误导。
- `hard_condition_sensitivity`：加入博士、五年全职等一票否决条件后是否正确降分。
- `relevant_core_skill_sensitivity`：加入与岗位职责直接相关、且简历未体现的核心技能后，系统是否合理降分或降低投递优先级。
- `preferred_skill_calibration`：加入明确标注为“非硬性”的加分技能后，系统是否避免过度扣分。
- `irrelevant_skill_robustness`：混入其他业务线的无关技术关键词后，结果是否保持稳定。
- `anchor_separation`：自动生成的高匹配与明显不匹配岗位能否拉开差距。

## 限制

这不是人工黄金集评估，不能证明真实 HR 一定认可排序，也不能计算真实 NDCG 或人工决策 F1。
V2 已将原来的单一“技能错配”拆分为相关核心技能、非硬性加分技能和无关技能污染三类测试。
它最适合用于：模型切换前后对比、Prompt 修改回归、检查硬条件与技能重要性校准、发现噪声敏感和检查幻觉风险。
"""
    md_path.write_text(md, encoding="utf-8")
    return json_path, csv_path, md_path


def main() -> int:
    args = parse_args()
    server_dir, output_dir = resolve_paths(args.output_dir)

    try:
        resume_text, source_jobs = load_source_data(
            server_dir,
            max(1, args.max_source_jobs),
        )
        cases = build_cases(resume_text, source_jobs)

        print("=" * 72)
        print("JobScout 零人工数据自动量化评估 V2")
        print(f"原始岗位数量：{len(source_jobs)}")
        print(f"自动测试样本：{len(cases)}")
        print(f"后端地址：{args.base_url}")
        print("=" * 72)

        timeout = httpx.Timeout(
            timeout=max(240.0, float(args.timeout)),
            connect=10.0,
        )
        with httpx.Client(
            base_url=args.base_url.rstrip("/"),
            timeout=timeout,
        ) as client:
            health = request_json(client, "GET", "/health")
            print(f"[健康检查] {health}")

            print("[1/4] 解析仓库自带示例简历")
            resume_id = import_resume(client, resume_text)
            print(f"resume_id={resume_id}")

            print("[2/4] 导入自动生成的岗位测试集")
            import_cases(client, cases)

            print("[3/4] 运行 JobScout 工作流")
            job_ids = [case.job_id for case in cases if case.job_id is not None]
            task_id, elapsed, final_task = run_workflow(
                client,
                resume_id,
                job_ids,
                args.timeout,
                args.poll_interval,
            )

            print("[4/4] 拉取结果并计算指标")
            raw_results = fetch_results(client, task_id)
            rows, _ = build_result_rows(cases, raw_results, resume_text)
            metrics, comparisons = evaluate_metrics(
                rows,
                args,
                elapsed,
                final_task,
            )
            json_path, csv_path, md_path = write_outputs(
                output_dir,
                metrics,
                rows,
                comparisons,
            )

        print("=" * 72)
        print(f"综合代理得分：{metrics['composite_score']} / 100")
        print(f"等级：{metrics['grade']}")
        for name, item in metrics["metrics"].items():
            print(f"- {name}: {item['value']:.2f}%")
        print(f"JSON：{json_path}")
        print(f"CSV ：{csv_path}")
        print(f"报告：{md_path}")
        print("=" * 72)
        return 0

    except KeyboardInterrupt:
        print("\n用户中止。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\n评估失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())