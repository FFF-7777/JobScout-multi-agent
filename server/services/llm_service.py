"""LLM 服务：按模型档位选择不同厂商 / Base URL / API Key。"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

from config import get_settings


class LLMConfigError(RuntimeError):
    """LLM 配置缺失。"""


class LLMOutputError(RuntimeError):
    """LLM 输出异常或连续重试后仍失败。"""


_clients: dict[tuple[str, str, int], OpenAI] = {}

_MAX_RETRIES = 3
_BASE_DELAY = 1.5


def _role_label(role: str) -> str:
    return {
        "fast": "快速档",
        "reasoning": "推理档",
        "report": "报告档",
        "vision": "视觉档",
        "ocr": "OCR 档",
        "fallback": "兜底档",
    }.get(role, role)


def _get_client(model_role: str) -> OpenAI:
    settings = get_settings()
    api_key = settings.resolve_api_key(model_role)
    base_url = settings.resolve_base_url(model_role)
    if not api_key:
        raise LLMConfigError(
            f"{_role_label(model_role)}未配置 API Key，请在项目根目录 .env 中填写对应的 LLM_*_API_KEY，或填写全局 LLM_API_KEY。"
        )
    key = (api_key, base_url, settings.llm_timeout)
    client = _clients.get(key)
    if client is None:
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=settings.llm_timeout,
        )
        _clients[key] = client
    return client


def _resolve_model(role: str) -> str:
    return get_settings().resolve_model(role)


def _resolve_provider(role: str) -> str:
    return get_settings().resolve_provider(role)


def _resolve_enable_thinking(role: str) -> bool:
    s = get_settings()
    return {
        # 产品合同：基础分析固定为快速模式，环境变量不能误开启思考。
        "fast": False,
        "reasoning": s.llm_reasoning_enable_thinking,
        "report": s.llm_report_enable_thinking,
    }.get(role, False)


def _normalize_search_strategy(search_strategy: str) -> str:
    strategy = (search_strategy or "auto").lower()
    if strategy in {"auto", "force", "forced"}:
        return "turbo"
    return strategy


def describe_role(role: str) -> dict[str, str | bool]:
    s = get_settings()
    return {
        "provider": _resolve_provider(role),
        "base_url": s.resolve_base_url(role),
        "model": s.resolve_model(role),
        "configured": s.role_configured(role),
        "enable_thinking": _resolve_enable_thinking(role),
    }


def _with_retry(fn):
    delay = _BASE_DELAY
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return fn()
        except RateLimitError as e:
            last_exc = e
            if attempt == _MAX_RETRIES - 1:
                raise LLMOutputError(f"触发限流（429），重试 {_MAX_RETRIES} 次仍失败：{e}") from e
            time.sleep(delay)
            delay *= 2
        except (APIConnectionError, APITimeoutError) as e:
            last_exc = e
            if attempt == _MAX_RETRIES - 1:
                raise LLMOutputError(f"LLM 连接/超时失败，重试后仍不可达：{e}") from e
            time.sleep(delay)
            delay *= 2
        except (BadRequestError, PermissionDeniedError) as e:
            raise LLMOutputError(str(e)) from e
    raise LLMOutputError(f"LLM 调用失败：{last_exc}") if last_exc else RuntimeError("LLM 重试耗尽")


def chat_text(
    system: str,
    user: str,
    temperature: float | None = None,
    model_role: str = "fast",
    *,
    enable_search: bool = False,
    forced_search: bool = False,
    search_strategy: str = "auto",
) -> str:
    settings = get_settings()
    client = _get_client(model_role)
    model = _resolve_model(model_role)
    fallback = _resolve_model("fallback")
    fallback_configured = settings.role_configured("fallback")
    use_fallback = fallback_configured and (
        settings.resolve_api_key("fallback") != settings.resolve_api_key(model_role)
        or settings.resolve_base_url("fallback") != settings.resolve_base_url(model_role)
        or fallback != model
    )
    enable_thinking = _resolve_enable_thinking(model_role)

    def _do(role: str, use_model: str) -> str:
        kwargs: dict[str, Any] = {
            "model": use_model,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        extra_body: dict[str, Any] = {}
        if enable_thinking and role == model_role:
            extra_body["enable_thinking"] = True
        if enable_search:
            extra_body["enable_search"] = True
            extra_body["search_options"] = {
                "forced_search": forced_search,
                "search_strategy": _normalize_search_strategy(search_strategy),
            }
        if extra_body:
            kwargs["extra_body"] = extra_body
        resp = _get_client(role).chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()

    try:
        return _with_retry(lambda: _do(model_role, model))
    except LLMOutputError:
        if use_fallback:
            return _with_retry(lambda: _do("fallback", fallback))
        raise


def chat_image_text(
    system: str,
    prompt: str,
    image: bytes,
    *,
    mime_type: str = "image/jpeg",
    model_role: str = "vision",
) -> str:
    """使用 OpenAI 兼容的多模态消息读取单张图片。"""
    client = _get_client(model_role)
    model = _resolve_model(model_role)
    data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"

    def _do() -> str:
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        )
        return (resp.choices[0].message.content or "").strip()

    text = _with_retry(_do)
    if not text:
        raise LLMOutputError("视觉模型未返回可用文字")
    return text


def chat_json_with_search_metadata(
    system: str,
    user: str,
    *,
    model_role: str = "reasoning",
    forced_search: bool = False,
    search_strategy: str = "auto",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """联网生成 JSON，并在 DashScope 原生协议下返回可核验搜索来源。"""
    settings = get_settings()
    provider = settings.resolve_provider(model_role).lower()
    if provider != "dashscope":
        data = chat_json(
            system,
            user,
            model_role=model_role,
            enable_search=True,
            forced_search=forced_search,
            search_strategy=search_strategy,
        )
        return data, {"performed": None, "sources": [], "provider": provider, "verifiable": False}

    def _do():
        client = _get_client(model_role)
        if hasattr(client, "responses"):
            return client.responses.create(
                model=_resolve_model(model_role),
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                tools=[{"type": "web_search"}, {"type": "web_extractor"}],
                extra_body={
                    "enable_thinking": _resolve_enable_thinking(model_role),
                    "enable_search": True,
                    "search_options": {
                        "forced_search": forced_search,
                        "search_strategy": _normalize_search_strategy(search_strategy),
                    },
                },
            )
        kwargs: dict[str, Any] = {
            "model": _resolve_model(model_role),
            "temperature": getattr(settings, "llm_temperature", 0),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "extra_body": {
                "enable_search": True,
                "search_options": {
                    "forced_search": forced_search,
                    "search_strategy": _normalize_search_strategy(search_strategy),
                },
            },
        }
        if _resolve_enable_thinking(model_role):
            kwargs["extra_body"]["enable_thinking"] = True
        return client.chat.completions.create(**kwargs)

    response = _with_retry(_do)
    raw_response = response.model_dump()
    content = (
        getattr(response, "output_text", None)
        or getattr(response.choices[0].message, "content", "")
        or ""
    ).strip()
    parsed = _try_parse(content)
    if parsed is None:
        raise LLMOutputError(f"联网模型未返回可解析的 JSON：{content[:500]}")
    output_items = raw_response.get("output") or raw_response
    sources_by_url: dict[str, dict[str, str]] = {}

    def collect_sources(value: Any) -> None:
        if isinstance(value, dict):
            url = str(value.get("url") or "").strip()
            if url.startswith(("http://", "https://")):
                sources_by_url[url] = {
                    "title": str(value.get("title") or value.get("site_name") or url).strip(),
                    "url": url,
                    "site_name": str(value.get("site_name") or "").strip(),
                    "published_at": str(value.get("published_at") or value.get("publish_time") or "").strip(),
                }
            for child in value.values():
                collect_sources(child)
        elif isinstance(value, list):
            for child in value:
                collect_sources(child)

    collect_sources(output_items)
    sources = list(sources_by_url.values())[:12]
    return parsed, {
        # DashScope compatible-mode chat responses may not expose a first-class
        # "web_search performed" flag in the pinned OpenAI SDK. For forced
        # search, treat a successful response as attempted/performed, while
        # keeping verifiability tied to actual URL sources.
        "performed": bool(sources) or forced_search,
        "sources": sources,
        "provider": provider,
        "verifiable": bool(sources),
    }


def chat_json(
    system: str,
    user: str,
    temperature: float | None = None,
    model_role: str = "fast",
    *,
    enable_search: bool = False,
    forced_search: bool = False,
    search_strategy: str = "auto",
) -> dict[str, Any]:
    settings = get_settings()
    client = _get_client(model_role)
    model = _resolve_model(model_role)
    fallback = _resolve_model("fallback")
    fallback_configured = settings.role_configured("fallback")
    use_fallback = fallback_configured and (
        settings.resolve_api_key("fallback") != settings.resolve_api_key(model_role)
        or settings.resolve_base_url("fallback") != settings.resolve_base_url(model_role)
        or fallback != model
    )
    enable_thinking = _resolve_enable_thinking(model_role)

    def _call(role: str, use_model: str, extra_hint: str = "") -> str:
        _use_response_format = not (enable_thinking and role == model_role)

        def _do() -> str:
            nonlocal _use_response_format
            kwargs: dict[str, Any] = {
                "model": use_model,
                "temperature": settings.llm_temperature if temperature is None else temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user + extra_hint},
                ],
            }
            extra_body: dict[str, Any] = {}
            if enable_thinking and role == model_role:
                extra_body["enable_thinking"] = True
            if enable_search:
                extra_body["enable_search"] = True
                extra_body["search_options"] = {
                    "forced_search": forced_search,
                    "search_strategy": _normalize_search_strategy(search_strategy),
                }
            if extra_body:
                kwargs["extra_body"] = extra_body
            elif _use_response_format:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                resp = _get_client(role).chat.completions.create(**kwargs)
            except BadRequestError:
                if not _use_response_format or (enable_thinking and role == model_role):
                    raise
                _use_response_format = False
                kwargs.pop("response_format", None)
                resp = _get_client(role).chat.completions.create(**kwargs)
            return (resp.choices[0].message.content or "").strip()

        return _with_retry(_do)

    raw = _call(model_role, model)
    parsed = _try_parse(raw)
    if parsed is not None:
        return parsed

    raw = _call(
        model_role,
        model,
        "\n\n请严格只输出一个合法的 JSON 对象，不要包含任何解释或 markdown 代码块标记。",
    )
    parsed = _try_parse(raw)
    if parsed is not None:
        return parsed

    if use_fallback:
        try:
            raw = _call("fallback", fallback)
        except LLMOutputError:
            raw = ""
        parsed = _try_parse(raw)
        if parsed is not None:
            return parsed

    raise LLMOutputError(f"模型未返回可解析的 JSON：{raw[:500]}")


def _try_parse(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
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
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
        return None


def ping() -> str:
    return chat_text("你是一个助手。", "请只回复两个字：可用")


def ping_role(model_role: str) -> str:
    return chat_text("你是一个助手。", "请只回复两个字：可用", model_role=model_role)
