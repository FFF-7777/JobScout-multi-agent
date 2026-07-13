"""并发执行工具：把一组 callable 限流并发跑在线程池里。

为什么用线程池而不是 asyncio.gather：
- 我们的 LLM 调用走的是 OpenAI SDK（同步 HTTP），FastAPI 是同步路由；
- 每个 callable 通常就只是一次 chat_json + JSON 解析，CPU 很轻；
- 线程池不需要改动 agent 函数的签名，对现有代码侵入最小；
- 限流用 Semaphore 实现，避免压垮 DashScope。

并发模型：
- worker 线程内只跑 callable + 序列化返回值，**绝不触碰 SQLAlchemy Session**。
- 调度端（主线程）按 future 完成顺序逐个落库，**保持单写者**，避免 SQLite 锁冲突。

API 约定：
- worker 接收单参数 item，返回单值 result（或 raise 异常）。
- on_result 收到 (item, result, error)：
  - worker 成功：error is None，result 是 worker 的返回值
  - worker 抛错：result is None，error 是 Exception 实例
- on_result 想主动中止整个流程：raise AbortFlow；bounded_map 会取消所有未完成
  future 并向上重新抛出（其它回调里也要 try/except 把 AbortFlow 透传出来）。
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import TypeVar

T = TypeVar("T")


class AbortFlow(BaseException):
    """on_result 抛此异常可中止整个 bounded_map 流程，剩余 worker 会被取消。"""


def bounded_map(
    items: list,
    worker: Callable[[object], T],
    *,
    max_concurrency: int = 3,
    on_result: Callable[[object, T | None, Exception | None], None] | None = None,
) -> list[tuple[object, T | None, Exception | None]]:
    """对 items 并发跑 worker，结果按完成顺序回调 on_result。

    worker 签名：worker(item) -> result
    on_result 签名：on_result(item, result, error) -> None
    """
    if not items:
        return []
    max_c = max(1, min(max_concurrency, len(items)))
    sem = threading.Semaphore(max_c)
    out: list[tuple[object, T | None, Exception | None]] = []
    out_lock = threading.Lock()

    def _run(item):
        sem.acquire()
        try:
            return item, worker(item), None
        except Exception as e:  # noqa: BLE001
            return item, None, e
        finally:
            sem.release()

    with ThreadPoolExecutor(max_workers=max_c) as pool:
        futures: list[Future] = [pool.submit(_run, it) for it in items]
        try:
            for fut in as_completed(futures):
                item, result, error = fut.result()
                with out_lock:
                    out.append((item, result, error))
                if on_result is not None:
                    try:
                        on_result(item, result, error)
                    except AbortFlow:
                        raise
                    except Exception as cb_err:  # noqa: BLE001
                        # 保留 worker 原始结果/错误，不因 on_result 回调异常而覆盖。
                        # out[-1] 中存有 worker 的 (item, result, error)，
                        # 回调出错不应篡改 worker 的真实执行结果。
                        pass
        except AbortFlow:
            for f in futures:
                f.cancel()
            raise
    return out
