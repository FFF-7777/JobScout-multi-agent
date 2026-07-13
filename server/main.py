"""JobScout 后端入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import get_settings
from database import SessionLocal, init_db
from models import AgentRun
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
    """进程重启后，把残留的 running 状态标记中断，避免前端轮询永久卡死。"""
    db = SessionLocal()
    try:
        n = (
            db.query(AgentRun)
            .filter(AgentRun.status == "running")
            .update(
                {
                    AgentRun.status: "failed",
                    AgentRun.error_message: "服务重启，任务已中断",
                    AgentRun.finished_at: None,
                }
            )
        )
        if n:
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
    agents = [
        {
            "name": "Resume Agent",
            "role": "fast",
            "model": s.llm_fast_model or s.llm_model,
            "provider": s.llm_fast_provider,
            "enable_thinking": s.llm_fast_enable_thinking,
            "configured": bool(s.dashscope_api_key),
        },
        {
            "name": "Job Agent",
            "role": "fast",
            "model": s.llm_fast_model or s.llm_model,
            "provider": s.llm_fast_provider,
            "enable_thinking": s.llm_fast_enable_thinking,
            "configured": bool(s.dashscope_api_key),
        },
        {
            "name": "Match Agent",
            "role": "reasoning",
            "model": s.llm_reasoning_model or s.llm_model,
            "provider": s.llm_reasoning_provider,
            "enable_thinking": s.llm_reasoning_enable_thinking,
            "configured": bool(s.dashscope_api_key),
        },
        {
            "name": "Report Agent",
            "role": "report",
            "model": s.llm_report_model or s.llm_reasoning_model or s.llm_model,
            "provider": s.llm_reasoning_provider,
            "enable_thinking": s.llm_report_enable_thinking,
            "configured": bool(s.dashscope_api_key),
        },
    ]
    return {
        "status": "ok",
        "llm_model": s.llm_model,
        "has_api_key": s.has_api_key,
        "llm_base_url": s.llm_base_url,
        "llm_timeout": s.llm_timeout,
        "agents": agents,
    }


@app.post("/api/test-llm", tags=["system"])
def test_llm():
    """调用一次模型自检，确认 API Key 与端点可用。"""
    try:
        reply = llm_service.ping()
        return {"ok": True, "model": settings.llm_model, "reply": reply}
    except llm_service.LLMConfigError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"LLM 调用失败：{e}") from e


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
