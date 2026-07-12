"""全局配置：从 .env 读取 LLM 与数据库设置。"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 阿里云百炼（通义千问）OpenAI 兼容端点
    dashscope_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_timeout: int = 120
    llm_temperature: float = 0.2

    # ── 模型档位：按任务难度分配，而非按 Agent 绑定厂商 ──
    # 设计原则：同一个主厂商（dashscope）支撑多个 Agent；
    # 简单提取任务用快速低成本模型，关键匹配任务用较强推理模型。
    # 留空则回退到 llm_model。跨厂商兜底（LLM_FALLBACK_*）为可选，
    # 目前复用同一个 OpenAI 兼容 client，仅在主模型持续失败时重试一次。
    llm_fast_model: str = ""         # Resume / Job / Report Agent：快速低成本
    llm_reasoning_model: str = ""    # Match Agent：较强推理模型
    llm_vision_model: str = ""       # 多模态兜底（预留，当前未启用）
    llm_fallback_model: str = ""     # 失败兜底（可选第二厂商）
    # 预留厂商字段：当前仅 dashscope 已接线，跨厂商切换为后续可选扩展点
    llm_fast_provider: str = "dashscope"
    llm_reasoning_provider: str = "dashscope"
    llm_vision_provider: str = "dashscope"
    llm_fallback_provider: str = "dashscope"

    # 数据库
    database_url: str = "sqlite:///./internscout.db"

    # CORS
    cors_origins: str = "*"

    # 工作流各 Agent 节点内并发调用 LLM 的上限（分开配置，避免盲目统一调高触发限流）
    # 设太高容易触发厂商限流 / 排队；从以下保守值起步，记录真实耗时与 429 再逐步调整。
    # 历史档（llm_concurrency）保留作兜底，新代码优先用下面的分项值。
    llm_concurrency: int = 3
    job_agent_concurrency: int = 6     # Job Agent：输入输出短，可稍高
    match_agent_concurrency: int = 4   # Match Agent：强模型、输入大，保守
    report_agent_concurrency: int = 6  # Report Agent：快速模型、输入精简后可稍高

    # 单次任务中「深度分析（full，额外结合简历原文）」岗位数上限
    # 仅在工作流启动时校验选中集合，不限制数据库里 full 总数
    full_mode_limit: int = 10

    # 报告自动生成策略：
    #   top_k -> 仅对匹配度最高的 N 个岗位生成（默认，避免为全部岗位消耗 LLM）
    #   none  -> 不自动生成，全部按需
    #   all   -> 为所有岗位生成（不推荐，耗时长）
    # 自动生成的报告默认用「基础报告」（代码模板，立即生成，无 LLM 调用）；
    # 深度 AI 报告通过 POST /api/reports/generate-batch 按需触发（mode=deep）。
    report_auto_policy: str = "top_k"
    report_auto_top_k: int = 5

    # 匹配两档策略（P1#7）：全量岗位先用「快速模型」出分排序（省 LLM 成本），
    # 仅匹配度最高的 N 个岗位再用「推理模型」做深度匹配。
    # 关闭（False）则全部岗位都用推理模型（旧行为）。
    match_two_tier: bool = True
    match_quick_top_k: int = 5

    # 百度 OCR（图片导入 JD 文字识别）
    baidu_ocr_app_id: str = ""
    baidu_ocr_api_key: str = ""
    baidu_ocr_secret_key: str = ""

    # OCR 服务商选择：baidu / tencent（控制图片导入走哪家）
    # 默认腾讯：免费档 10 QPS（百度仅 ~2 QPS），20 张图约 2s 而非 10s
    ocr_provider: str = "tencent"
    # 腾讯云 OCR（备选）
    tencent_ocr_secret_id: str = ""
    tencent_ocr_secret_key: str = ""

    @property
    def has_api_key(self) -> bool:
        return bool(self.dashscope_api_key and self.dashscope_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
