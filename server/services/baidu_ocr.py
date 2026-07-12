"""百度 OCR 服务：调用百度通用文字识别（高精度）接口，提取图片中的文本。

依赖 httpx（已在 requirements.txt），无需额外安装 baidu-aip SDK。
配置项（.env）：
  BAIDU_OCR_APP_ID       - 百度 AI 开放平台应用 ID（获取 token 时需要，部分接口用）
  BAIDU_OCR_API_KEY      - 百度 AI 开放平台 API Key
  BAIDU_OCR_SECRET_KEY   - 百度 AI 开放平台 Secret Key

免费额度：通用高精度版 50 次/天；基础版 1000 次/天。
文档：https://ai.baidu.com/docs#/OCR-API/top
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── 全局 token 缓存 ───────────────────────────────────────────────
_cached_token: str = ""
_cached_token_at: float = 0
_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
# 百度 access_token 有效期约 30 天（2592000 秒），提前 1 天刷新
_TOKEN_EXPIRE = 2592000 - 86400


def _get_app_id() -> str:
    from config import get_settings

    return getattr(get_settings(), "baidu_ocr_app_id", "")


def _get_api_key() -> str:
    from config import get_settings

    return getattr(get_settings(), "baidu_ocr_api_key", "")


def _get_secret_key() -> str:
    from config import get_settings

    return getattr(get_settings(), "baidu_ocr_secret_key", "")


async def _fetch_access_token() -> str:
    """获取百度 OAuth access_token（带内存缓存）。"""
    global _cached_token, _cached_token_at

    now = time.time()
    if _cached_token and (now - _cached_token_at) < _TOKEN_EXPIRE:
        return _cached_token

    api_key = _get_api_key()
    secret_key = _get_secret_key()
    if not api_key or not secret_key:
        raise ValueError(
            "百度 OCR 未配置：请在 .env 中设置 BAIDU_OCR_API_KEY 和 BAIDU_OCR_SECRET_KEY。"
            "获取地址：https://console.bce.baidu.com/qianjin/aim/create"
        )

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _TOKEN_URL,
            params={
                "grant_type": "client_credentials",
                "client_id": api_key,
                "client_secret": secret_key,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        raise ValueError(f"百度 OCR 获取 Token 失败：{data.get('error_description', data['error'])}")

    _cached_token = data["access_token"]
    _cached_token_at = now
    logger.info("百度 OCR access_token 已刷新")
    return _cached_token


async def recognize_image(image_bytes: bytes, filename: str = "") -> str:
    """对单张图片执行通用文字识别（高精度版），返回识别出的纯文本。

    Args:
        image_bytes: 图片原始字节（PNG/JPEG/JPG/BMP）
        filename: 文件名（仅用于日志）

    Returns:
        拼接后的文本，每段用换行分隔。识别失败抛异常。

    Raises:
        ValueError: 配置缺失或 API 返回错误
        httpx.HTTPStatusError: 网络请求失败
    """
    token = await _fetch_access_token()

    # 高精度通用文字识别接口
    url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic?access_token={token}"

    # 百度 OCR 要求 base64 编码
    b64 = base64.b64encode(image_bytes).decode("ascii")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data={"image": b64})
        resp.raise_for_status()
        result = resp.json()

    error_code = result.get("error_code")
    if error_code:
        error_msg = result.get("error_msg", "未知错误")
        # 常见错误码友好提示
        hints = {
            17: "每天调用量超出限额",
            18: "QPS 超限（请稍后重试）",
            19: "请求超时",
            100: "无效的参数（图片可能损坏或格式不支持）",
            2835: "图片过大（base64 后不超过 10MB）",
        }
        hint = hints.get(error_code, "")
        raise ValueError(f"百度 OCR 识别失败[{error_code}]：{error_msg}{'（' + hint + '）' if hint else ''}")

    words_result = result.get("words_result", [])
    if not words_result:
        logger.warning("图片 %s OCR 未识别到文字", filename)
        return ""

    # 把每一行的 text 用换行拼接
    lines = [block["words"] for block in words_result if block.get("words")]
    text = "\n".join(lines)
    logger.info("图片 %s OCR 识别到 %d 行文字，共 %d 字符", filename, len(lines), len(text))
    return text


async def recognize_images_batch(
    images: list[tuple[bytes, str]], max_concurrency: int = 5
) -> list[dict]:
    """批量识别多张图片。

    Args:
        images: [(image_bytes, filename), ...]
        max_concurrency: 并发上限

    Returns:
        [{"filename": ..., "text": ...}, {"filename": ..., "error": ...}, ...] 顺序与输入一致
    """
    import asyncio

    sem = asyncio.Semaphore(max_concurrency)

    async def _one(idx: int, data: bytes, name: str) -> tuple[int, dict]:
        async with sem:
            try:
                text = await recognize_image(data, name)
                return idx, {"filename": name, "text": text}
            except Exception as e:
                logger.error("图片 %s OCR 失败：%s", name, e)
                return idx, {"filename": name, "error": str(e)}

    tasks = [_one(i, d, n) for i, (d, n) in enumerate(images)]
    results = await asyncio.gather(*tasks)
    # 按 index 排序恢复原顺序
    results.sort(key=lambda x: x[0])
    return [r[1] for r in results]
