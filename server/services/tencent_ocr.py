"""腾讯云 OCR 服务：调用通用印刷体识别（高精度 GeneralAccurateOCR）接口，提取图片文本。

依赖 tencentcloud-sdk-python-ocr（已在 requirements.txt）。
配置项（.env）：
  TENCENT_OCR_SECRET_ID   - 腾讯云 SecretId
  TENCENT_OCR_SECRET_KEY  - 腾讯云 SecretKey

免费额度：通用文字识别 1,000 次/月（仅当月有效，次月重置）。
文档：https://cloud.tencent.com/document/product/866/34937
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time

logger = logging.getLogger(__name__)


class _RateLimiter:
    """简单令牌桶：把平均发起速率压到 rate 次/秒以内，规避服务商 QPS 限流。"""

    def __init__(self, rate: float):
        self.interval = 1.0 / rate if rate and rate > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def acquire(self):
        if self.interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            if self._next < now:
                self._next = now + self.interval
            else:
                wait = self._next - now
                self._next += self.interval
                await asyncio.sleep(wait)


def _is_throttle(err: Exception) -> bool:
    """判断异常是否为 QPS / 频率限流（可重试）。"""
    s = str(err).lower()
    return ("qps" in s) or ("requestlimitexceeded" in s) or ("frequency limit" in s)

_ENDPOINT = "ocr.tencentcloudapi.com"
_REGION = "ap-guangzhou"


def _get_secret_id() -> str:
    from config import get_settings

    return getattr(get_settings(), "tencent_ocr_secret_id", "")


def _get_secret_key() -> str:
    from config import get_settings

    return getattr(get_settings(), "tencent_ocr_secret_key", "")


def _recognize_sync(image_bytes: bytes) -> str:
    """同步识别单张图片（在 to_thread 中调用，避免阻塞事件循环）。"""
    from tencentcloud.common import credential
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.ocr.v20181119 import ocr_client, models

    secret_id = _get_secret_id()
    secret_key = _get_secret_key()
    if not secret_id or not secret_key:
        raise ValueError(
            "腾讯云 OCR 未配置：请在 .env 中设置 TENCENT_OCR_SECRET_ID 和 TENCENT_OCR_SECRET_KEY。"
            "获取地址：https://console.cloud.tencent.com/cam/capi"
        )

    cred = credential.Credential(secret_id, secret_key)
    http_profile = HttpProfile()
    http_profile.endpoint = _ENDPOINT
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    client = ocr_client.OcrClient(cred, _REGION, client_profile)

    req = models.GeneralAccurateOCRRequest()
    req.ImageBase64 = base64.b64encode(image_bytes).decode("ascii")

    resp = client.GeneralAccurateOCR(req)
    texts = [d.DetectedText for d in (resp.TextDetections or []) if d.DetectedText]
    return "\n".join(texts)


async def recognize_image(image_bytes: bytes, filename: str = "") -> str:
    """对单张图片执行腾讯云通用印刷体（高精度）识别，返回文本。

    Args:
        image_bytes: 图片原始字节
        filename: 文件名（仅日志用）

    Returns:
        拼接后的文本（每行一条），识别失败抛异常。
    """
    try:
        text = await asyncio.to_thread(_recognize_sync, image_bytes)
    except Exception as e:
        # 腾讯云 SDK 抛 TencentCloudSDKException，信息已含 Code/Message
        err = str(e)
        logger.error("图片 %s 腾讯 OCR 失败：%s", filename, err)
        # 友好提示：免费额度耗尽等
        if "ResourceInsufficient" in err or "FailedOperation" in err:
            raise ValueError("腾讯云 OCR 调用失败：可能免费额度已耗尽或图片无法识别") from e
        raise ValueError(f"腾讯云 OCR 识别失败：{err}") from e

    logger.info("图片 %s 腾讯 OCR 识别到 %d 字符", filename, len(text))
    return text


async def recognize_images_batch(
    images: list[tuple[bytes, str]],
    max_concurrency: int = 10,
    rate_per_sec: float = 10.0,
    max_retries: int = 4,
) -> list[dict]:
    """批量识别多张图片（带速率限制 + 限流重试），顺序与输入一致。

    Args:
        images: [(image_bytes, filename), ...]
        max_concurrency: 同时在途请求数上限
        rate_per_sec: 平均发起速率上限（次/秒）。腾讯免费档硬性 10 QPS，
                      超出报 RequestLimitExceeded，故默认 10 并配合重试兜底。
        max_retries: 命中 QPS 限流时的重试次数

    Returns:
        [{"filename": ..., "text": ...}, {"filename": ..., "error": ...}, ...]
    """
    sem = asyncio.Semaphore(max_concurrency)
    limiter = _RateLimiter(rate_per_sec)

    async def _one(idx: int, data: bytes, name: str) -> tuple[int, dict]:
        async with sem:
            last_err = None
            for attempt in range(max_retries + 1):
                await limiter.acquire()
                try:
                    text = await recognize_image(data, name)
                    return idx, {"filename": name, "text": text}
                except Exception as e:
                    last_err = e
                    if _is_throttle(e) and attempt < max_retries:
                        await asyncio.sleep(0.5 * (2 ** attempt))
                        continue
                    logger.error("图片 %s 腾讯 OCR 失败：%s", name, e)
                    return idx, {"filename": name, "error": str(e)}
            return idx, {"filename": name, "error": f"重试后仍失败: {last_err}"}

    tasks = [_one(i, d, n) for i, (d, n) in enumerate(images)]
    results = await asyncio.gather(*tasks)
    results.sort(key=lambda x: x[0])
    return [r[1] for r in results]
