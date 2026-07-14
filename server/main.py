"""JobScout 后端入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import get_settings
from database import SessionLocal, init_db
from models import AgentRun, ReportTask
from routers import agents, jobs, match, reports, resumes
from services import llm_service
from services.job_parse_queue import start_parse_daemon, stop_parse_daemon

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _recover_interrupted_runs()
    start_parse_daemon()
    yield
    stop_parse_daemon()


def _recover_interrupted_runs() -> None:
    """进程重启后终结未完成任务，避免前端轮询永久卡死。"""
    db = SessionLocal()
    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        agent_count = (
            db.query(AgentRun)
            .filter(AgentRun.status.in_(("pending", "running")))
            .update(
                {
                    AgentRun.status: "failed",
                    AgentRun.error_message: "服务重启，任务已中断",
                    AgentRun.progress: 100,
                    AgentRun.finished_at: now,
                },
                synchronize_session=False,
            )
        )
        report_count = (
            db.query(ReportTask)
            .filter(ReportTask.status.in_(("queued", "running")))
            .update(
                {
                    ReportTask.status: "failed",
                    ReportTask.finished_at: now,
                },
                synchronize_session=False,
            )
        )
        if agent_count or report_count:
            db.commit()
    finally:
        db.close()


app = FastAPI(
    title="JobScout API",
    description="AI 求职岗位筛选与匹配多智能体助手 —— 4 Agent + LangGraph 工作流",
    version="1.0.0",
    lifespan=lifespan,
)

_origins = (
    ["*"]
    if settings.cors_origins.strip() == "*"
    else [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
)
# 当 origins 为 "*" 时不能带 credentials（浏览器会拒绝该组合），故联动关闭
_allow_credentials = settings.cors_origins.strip() != "*"
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(resumes.router)
app.include_router(jobs.router)
app.include_router(match.router)
app.include_router(agents.router)
app.include_router(reports.router)


@app.exception_handler(llm_service.LLMConfigError)
async def _handle_llm_config(request: Request, exc: llm_service.LLMConfigError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(llm_service.LLMOutputError)
async def _handle_llm_output(request: Request, exc: llm_service.LLMOutputError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.get("/health", tags=["system"])
def health():
    s = settings
    # 静态探活：列每个 Agent 当前实际用的模型 + 思考模式 + 配置状态。
    # 注意：这是同步接口，不实际调用 LLM；模型可达性需另调 POST /api/test-llm。
    fast_info = llm_service.describe_role("fast")
    reasoning_info = llm_service.describe_role("reasoning")
    report_info = llm_service.describe_role("report")
    agents = [
        {
            "name": "Resume Agent",
            "role": "fast",
            "model": fast_info["model"],
            "provider": fast_info["provider"],
            "enable_thinking": fast_info["enable_thinking"],
            "network_access": "disabled",
            "configured": fast_info["configured"],
        },
        {
            "name": "Job Agent",
            "role": "fast",
            "model": fast_info["model"],
            "provider": fast_info["provider"],
            "enable_thinking": fast_info["enable_thinking"],
            "network_access": "disabled",
            "configured": fast_info["configured"],
        },
        {
            "name": "Match Agent",
            "role": "fast + reasoning" if s.match_two_tier else "reasoning",
            "model": (
                f"quick: {fast_info['model']} / "
                f"deep: {reasoning_info['model']}"
                if s.match_two_tier
                else reasoning_info["model"]
            ),
            "provider": (
                f"quick: {fast_info['provider']} / deep: {reasoning_info['provider']}"
                if s.match_two_tier
                else reasoning_info["provider"]
            ),
            "enable_thinking": reasoning_info["enable_thinking"],
            "network_access": "deep_forced_with_model_fallback",
            "configured": bool(fast_info["configured"] and reasoning_info["configured"])
            if s.match_two_tier
            else reasoning_info["configured"],
        },
        {
            "name": "Report Agent",
            "role": "report",
            "model": report_info["model"],
            "provider": report_info["provider"],
            "enable_thinking": report_info["enable_thinking"],
            "network_access": "forced_with_model_fallback",
            "configured": report_info["configured"],
        },
    ]
    return {
        "status": "ok",
        "llm_model": s.llm_model,
        "has_api_key": s.has_api_key,
        "llm_base_url": s.llm_base_url,
        "llm_timeout": s.llm_timeout,
        "agents": agents,
        "network_capabilities": {
            "quick_analysis": "disabled",
            "deep_analysis": "forced_with_model_fallback",
            "deep_report": "forced_with_model_fallback",
        },
    }


@app.post("/api/test-llm", tags=["system"])
def test_llm():
    """逐个测试各智能体当前实际使用的模型连通性。"""
    try:
        checks = [
            ("Resume Agent", "fast", settings.resolve_model("fast")),
            ("Job Agent", "fast", settings.resolve_model("fast")),
        ]
        if settings.match_two_tier:
            checks.extend(
                [
                    ("Match Agent (Quick)", "fast", settings.resolve_model("fast")),
                    ("Match Agent (Deep)", "reasoning", settings.resolve_model("reasoning")),
                ]
            )
        else:
            checks.append(("Match Agent", "reasoning", settings.resolve_model("reasoning")))
        checks.append(("Report Agent", "report", settings.resolve_model("report")))
        results = []
        for name, role, model in checks:
            try:
                reply = llm_service.ping_role(role)
                results.append(
                    {
                        "name": name,
                        "ok": True,
                        "model": model,
                        "provider": settings.resolve_provider(role),
                        "reply": reply,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "name": name,
                        "ok": False,
                        "model": model,
                        "provider": settings.resolve_provider(role),
                        "reply": str(exc),
                    }
                )
        return {
            "ok": all(item["ok"] for item in results),
            "results": results,
        }
    except llm_service.LLMConfigError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"LLM 调用失败：{e}") from e


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
