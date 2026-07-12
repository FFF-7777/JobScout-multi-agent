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

    # 工作流：Job / Match / Report 节点内并发调用 LLM 的上限
    # 设太高容易触发 DashScope 限流；默认 3，留 env 调节
    llm_concurrency: int = 3

    # 百度 OCR（图片导入 JD 文字识别）
    baidu_ocr_app_id: str = ""
    baidu_ocr_api_key: str = ""
    baidu_ocr_secret_key: str = ""

    # OCR 服务商选择：baidu / tencent（控制图片导入走哪家）
    ocr_provider: str = "baidu"
    # 腾讯云 OCR（备选）
    tencent_ocr_secret_id: str = ""
    tencent_ocr_secret_key: str = ""

    @property
    def has_api_key(self) -> bool:
        return bool(self.dashscope_api_key and self.dashscope_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
