"""全局配置：从 .env 读取 LLM、OCR 与数据库配置。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    # 兼容旧配置：默认仍以 DashScope 为主；新配置可直接使用 LLM_API_KEY / LLM_PROVIDER / LLM_BASE_URL
    dashscope_api_key: str = ""
    llm_provider: str = "dashscope"
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_timeout: int = 120
    llm_temperature: float = 0.2

    # 各模型档位：允许分别接入不同厂商、不同 Base URL、不同 API Key
    llm_fast_model: str = ""
    llm_fast_provider: str = ""
    llm_fast_base_url: str = ""
    llm_fast_api_key: str = ""

    llm_reasoning_model: str = ""
    llm_reasoning_provider: str = ""
    llm_reasoning_base_url: str = ""
    llm_reasoning_api_key: str = ""

    llm_report_model: str = ""
    llm_report_provider: str = ""
    llm_report_base_url: str = ""
    llm_report_api_key: str = ""

    llm_vision_model: str = ""
    llm_vision_provider: str = ""
    llm_vision_base_url: str = ""
    llm_vision_api_key: str = ""

    llm_ocr_model: str = ""
    llm_ocr_provider: str = ""
    llm_ocr_base_url: str = ""
    llm_ocr_api_key: str = ""

    llm_fallback_model: str = ""
    llm_fallback_provider: str = ""
    llm_fallback_base_url: str = ""
    llm_fallback_api_key: str = ""

    # 思考模式开关
    llm_reasoning_enable_thinking: bool = True
    llm_report_enable_thinking: bool = False

    # 数据库
    database_url: str = "sqlite:///./internscout.db"

    # CORS
    cors_origins: str = "*"

    # 并发
    llm_concurrency: int = 3
    job_agent_concurrency: int = 6
    match_agent_concurrency: int = 4
    report_agent_concurrency: int = 6

    # 深度分析岗位数上限
    full_mode_limit: int = 0

    # 报告自动生成策略
    report_auto_policy: str = "top_k"
    report_auto_top_k: int = 5
    report_history_limit: int = 100

    # 两段式匹配
    match_two_tier: bool = True

    # 深度分析与深度报告固定强制尝试联网；这里只配置单次研究查询上限。
    deep_research_max_items: int = 6

    # 百度 OCR
    baidu_ocr_app_id: str = ""
    baidu_ocr_api_key: str = ""
    baidu_ocr_secret_key: str = ""

    # OCR 服务商
    ocr_provider: str = "tencent"
    tencent_ocr_secret_id: str = ""
    tencent_ocr_secret_key: str = ""
    tencent_ocr_concurrency: int = 10
    tencent_ocr_rate_per_sec: float = 10.0
    baidu_ocr_concurrency: int = 3
    baidu_ocr_rate_per_sec: float = 2.0
    vision_ocr_concurrency: int = 2

    def _global_api_key(self) -> str:
        return (self.llm_api_key or self.dashscope_api_key or "").strip()

    def _slot_value(self, role: str, kind: str) -> str:
        return (getattr(self, f"llm_{role}_{kind}", "") or "").strip()

    def resolve_model(self, role: str) -> str:
        if role == "fast":
            return self.llm_fast_model or self.llm_model
        if role == "reasoning":
            return self.llm_reasoning_model or self.llm_model
        if role == "report":
            return self.llm_report_model or self.llm_reasoning_model or self.llm_model
        if role == "vision":
            return self.llm_vision_model or self.llm_model
        if role == "ocr":
            return self.llm_ocr_model or self.llm_model
        if role == "fallback":
            return self.llm_fallback_model or self.llm_model
        return self.llm_model

    def resolve_provider(self, role: str) -> str:
        slot = self._slot_value(role, "provider")
        return slot or self.llm_provider or "openai-compatible"

    def resolve_base_url(self, role: str) -> str:
        slot = self._slot_value(role, "base_url")
        return slot or self.llm_base_url

    def resolve_api_key(self, role: str) -> str:
        slot = self._slot_value(role, "api_key")
        return slot or self._global_api_key()

    def role_configured(self, role: str) -> bool:
        return bool(self.resolve_api_key(role))

    @property
    def has_api_key(self) -> bool:
        roles = ("fast", "reasoning", "report", "vision", "ocr", "fallback")
        return bool(self._global_api_key()) or any(self.role_configured(role) for role in roles)


@lru_cache
def get_settings() -> Settings:
    return Settings()
