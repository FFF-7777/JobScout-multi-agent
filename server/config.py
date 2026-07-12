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

    # 数据库
    database_url: str = "sqlite:///./internscout.db"

    # CORS
    cors_origins: str = "*"

    @property
    def has_api_key(self) -> bool:
        return bool(self.dashscope_api_key and self.dashscope_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
