"""LLM 服务：封装阿里云百炼（通义千问）OpenAI 兼容调用，返回结构化 JSON。

设计要点：
- 纯 LLM 实现，缺少 API Key 时抛出 LLMConfigError（不做静默降级）。
- chat_json() 强制模型输出 JSON 对象，解析失败时重试一次。
- 网络错误（连接/超时）与限流（429）自动指数退避重试，避免偶发抖动导致任务整体失败。
- 所有重试后仍失败的「可恢复错误」统一包装为 LLMOutputError，由调用方/全局处理器转成可读错误。
"""
from __future__ import annotations

import json
import time
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from config import get_settings


class LLMConfigError(RuntimeError):
    """LLM 配置缺失（如未设置 API Key）。"""


class LLMOutputError(RuntimeError):
    """LLM 返回内容无法解析为期望的 JSON，或重试后仍网络/限流失败。"""


_client: OpenAI | None = None

_MAX_RETRIES = 3
_BASE_DELAY = 1.5


def _get_client() -> OpenAI:
    global _client
    settings = get_settings()
    if not settings.has_api_key:
        raise LLMConfigError(
            "未配置 DASHSCOPE_API_KEY，无法调用大模型。请在 server/.env 中填写有效的百炼 API Key。"
        )
    if _client is None:
        _client = OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout,
        )
    return _client


def _resolve_model(role: str) -> str:
    """把模型角色映射成具体模型名；未配置时回退到全局 llm_model。

    角色：fast（快速低成本）/ reasoning（强推理+思考）/
          report（报告生成）/ vision（多模态兜底，预留）/
          ocr（截图识别）/ fallback（故障切换）。
    """
    s = get_settings()
    mapping = {
        "fast": s.llm_fast_model or s.llm_model,
        "reasoning": s.llm_reasoning_model or s.llm_model,
        "report": s.llm_report_model or s.llm_reasoning_model or s.llm_model,
        "vision": s.llm_vision_model or s.llm_model,
        "ocr": s.llm_ocr_model or s.llm_model,
        "fallback": s.llm_fallback_model or s.llm_model,
    }
    return mapping.get(role, s.llm_model)


def _resolve_enable_thinking(role: str) -> bool:
    """根据角色判断是否启用思考模式。"""
    s = get_settings()
    mapping = {
        "fast": s.llm_fast_enable_thinking,
        "reasoning": s.llm_reasoning_enable_thinking,
        "report": s.llm_report_enable_thinking,
    }
    return mapping.get(role, False)


def _with_retry(fn):
    """对网络错误与限流做指数退避重试；不可恢复错误直接抛出。"""
    delay = _BASE_DELAY
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return fn()
        except RateLimitError as e:  # 429 限流
            last_exc = e
            if attempt == _MAX_RETRIES - 1:
                raise LLMOutputError(
                    f"触发限流（429），重试 {_MAX_RETRIES} 次仍失败：{e}"
                ) from e
            time.sleep(delay)
            delay *= 2
        except (APIConnectionError, APITimeoutError) as e:  # 网络抖动/超时
            last_exc = e
            if attempt == _MAX_RETRIES - 1:
                raise LLMOutputError(
                    f"LLM 连接/超时失败，重试后仍不可达：{e}"
                ) from e
            time.sleep(delay)
            delay *= 2
    raise LLMOutputError(f"LLM 调用失败：{last_exc}") if last_exc else RuntimeError(
        "LLM 重试耗尽"
    )


def chat_text(
    system: str, user: str, temperature: float | None = None, model_role: str = "fast"
) -> str:
    """普通文本对话。model_role 决定用哪一档模型（默认 fast）。"""
    settings = get_settings()
    client = _get_client()
    model = _resolve_model(model_role)
    fallback = _resolve_model("fallback")
    use_fallback = bool(fallback) and fallback != model
    enable_thinking = _resolve_enable_thinking(model_role)

    def _do(use_model: str) -> str:
        kwargs: dict = {
            "model": use_model,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if enable_thinking:
            kwargs["extra_body"] = {"enable_thinking": True}
        resp = client.chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()

    try:
        return _with_retry(lambda: _do(model))
    except LLMOutputError:
        if use_fallback:
            return _with_retry(lambda: _do(fallback))
        raise


def chat_json(
    system: str,
    user: str,
    temperature: float | None = None,
    model_role: str = "fast",
) -> dict[str, Any]:
    """要求模型输出 JSON 对象，返回解析后的 dict。失败重试一次。

    model_role：fast / reasoning / report / vision / fallback。
    思考模式下跳过 response_format（与 reasoning 不兼容），改用 prompt 指令约束 JSON。
    """
    settings = get_settings()
    client = _get_client()
    model = _resolve_model(model_role)
    fallback = _resolve_model("fallback")
    use_fallback = bool(fallback) and fallback != model
    enable_thinking = _resolve_enable_thinking(model_role)

    def _call(use_model: str, extra_hint: str = "") -> str:
        def _do() -> str:
            kwargs: dict = {
                "model": use_model,
                "temperature": settings.llm_temperature
                if temperature is None
                else temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user + extra_hint},
                ],
            }
            if enable_thinking:
                # 思考模式与 response_format 不兼容，用 prompt 强制 JSON
                kwargs["extra_body"] = {"enable_thinking": True}
            else:
                kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**kwargs)
            return (resp.choices[0].message.content or "").strip()

        return _with_retry(_do)

    raw = _call(model)
    parsed = _try_parse(raw)
    if parsed is not None:
        return parsed

    # 重试一次，强调只输出 JSON
    raw = _call(model, "\n\n请严格只输出一个合法的 JSON 对象，不要包含任何解释或 markdown 代码块标记。")
    parsed = _try_parse(raw)
    if parsed is not None:
        return parsed

    if use_fallback:
        # 主模型两次都失败 -> 用兜底模型整段再试一次
        try:
            raw = _call(fallback)
        except LLMOutputError:
            raw = ""
        parsed = _try_parse(raw)
        if parsed is not None:
            return parsed
        try:
            raw = _call(
                fallback,
                "\n\n请严格只输出一个合法的 JSON 对象，不要包含任何解释或 markdown 代码块标记。",
            )
        except LLMOutputError:
            raw = ""
        parsed = _try_parse(raw)
        if parsed is not None:
            return parsed

    raise LLMOutputError(f"模型未返回可解析的 JSON：{raw[:500]}")


def _try_parse(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    # 去除可能的 ```json ... ``` 包裹
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        # 尝试截取第一个 { 到最后一个 }
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
        return None


def ping() -> str:
    """自检：调用一次模型确认可用。"""
    return chat_text("你是一个助手。", "请只回复两个字：可用")
