"""腾讯 OCR → 百度 OCR → 视觉模型的受控并发降级链路。"""
from __future__ import annotations

import asyncio
import time

from config import get_settings
from services import baidu_ocr, tencent_ocr, vision_ocr
from services.jd_preprocessor import assess_ocr_quality


def _quality(text: str, document_type: str) -> float:
    if document_type == "job":
        return assess_ocr_quality(text)
    text = (text or "").strip()
    if not text:
        return 0.0
    score = min(len(text) / 800, 0.6)
    score += 0.2 if any(k in text for k in ("教育", "项目", "技能", "经历")) else 0
    score += 0.2 if "�" not in text else 0
    return min(score, 1.0)


async def recognize_images(
    images: list[tuple[bytes, str]],
    *,
    document_type: str,
) -> list[dict]:
    settings = get_settings()
    started = time.monotonic()
    results = [
        {"filename": name, "text": "", "error": "", "audit": {"attempts": []}}
        for _, name in images
    ]

    async def run_provider(provider: str, indexes: list[int]) -> None:
        if not indexes:
            return
        subset = [images[i] for i in indexes]
        batch_started = time.monotonic()
        if provider == "tencent":
            batch = await tencent_ocr.recognize_images_batch(
                subset,
                max_concurrency=max(1, settings.tencent_ocr_concurrency),
                rate_per_sec=max(0.1, settings.tencent_ocr_rate_per_sec),
            )
        else:
            batch = await baidu_ocr.recognize_images_batch(
                subset,
                max_concurrency=max(1, settings.baidu_ocr_concurrency),
                rate_per_sec=max(0.1, settings.baidu_ocr_rate_per_sec),
            )
        elapsed = int((time.monotonic() - batch_started) * 1000)
        per_item = max(0, elapsed // max(len(indexes), 1))
        for index, item in zip(indexes, batch):
            text = (item.get("text") or "").strip()
            error = str(item.get("error") or "").strip()
            quality = _quality(text, document_type)
            results[index]["audit"]["attempts"].append({
                "provider": provider,
                "status": "success" if text else "failed",
                "quality_score": quality,
                "duration_ms": per_item,
                "error": error,
            })
            if text:
                results[index]["text"] = text
                results[index]["error"] = ""
            elif error:
                results[index]["error"] = error

    all_indexes = list(range(len(images)))
    await run_provider("tencent", all_indexes)
    baidu_indexes = [i for i, item in enumerate(results) if not item["text"]]
    await run_provider("baidu", baidu_indexes)

    vision_indexes = [i for i, item in enumerate(results) if not item["text"]]
    sem = asyncio.Semaphore(max(1, settings.vision_ocr_concurrency))

    async def run_vision(index: int) -> None:
        data, name = images[index]
        async with sem:
            attempt_started = time.monotonic()
            try:
                text = await asyncio.to_thread(
                    vision_ocr.recognize_image,
                    data,
                    name,
                    document_type=document_type,
                )
                text = text.strip()
                error = "" if text else "视觉模型未返回文字"
            except Exception as exc:  # noqa: BLE001
                text = ""
                error = str(exc)
            results[index]["audit"]["attempts"].append({
                "provider": "vision",
                "status": "success" if text else "failed",
                "quality_score": _quality(text, document_type),
                "duration_ms": int((time.monotonic() - attempt_started) * 1000),
                "error": error,
            })
            if text:
                results[index]["text"] = text
                results[index]["error"] = ""
            else:
                prior = results[index]["error"]
                results[index]["error"] = f"{prior}；视觉兜底失败：{error}" if prior else error

    await asyncio.gather(*(run_vision(i) for i in vision_indexes))

    total_elapsed = int((time.monotonic() - started) * 1000)
    for item in results:
        attempts = item["audit"]["attempts"]
        success = next((a for a in reversed(attempts) if a["status"] == "success"), None)
        item["audit"].update({
            "final_provider": success["provider"] if success else "",
            "fallback_level": max(0, len(attempts) - 1),
            "quality_score": success["quality_score"] if success else 0.0,
            "total_batch_duration_ms": total_elapsed,
        })
    return results
