"""真实服务端到端验收脚本。

先启动后端并配置可用模型，再手动执行：
    python e2e_test.py

该文件不是 pytest 测试模块；导入时不会访问网络或修改数据库。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

BASE_URL = os.environ.get("E2E_BASE", "http://127.0.0.1:8020")
SAMPLE_DIR = Path(__file__).resolve().parent / "sample_data"


def checked(response: httpx.Response) -> dict | list:
    response.raise_for_status()
    return response.json()


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=180) as client:
        resume_text = (SAMPLE_DIR / "sample_resume.md").read_text(encoding="utf-8")
        response = client.post(
            "/api/resumes/parse",
            json={"text": resume_text, "filename": "sample_resume.md"},
        )
        resume_id = checked(response)["id"]
        print(f"简历解析完成：{resume_id}", flush=True)

        csv_bytes = (SAMPLE_DIR / "sample_jobs.csv").read_bytes()
        response = client.post(
            "/api/jobs/import-file",
            files={"file": ("sample_jobs.csv", csv_bytes, "text/csv")},
        )
        jobs = checked(response)
        job_ids = [job["id"] for job in jobs]
        print(f"岗位导入完成：{len(job_ids)} 个", flush=True)

        task = checked(
            client.post(
                "/api/agents/run",
                json={"resume_id": resume_id, "job_ids": job_ids},
            )
        )
        task_id = task["task_id"]
        print(f"分析任务已创建：{task_id}", flush=True)

        for index in range(200):
            task = checked(client.get(f"/api/agents/tasks/{task_id}"))
            steps = "  ".join(
                f'{step["agent_name"]}:{step["status"]}({step.get("progress", 0)})'
                for step in task["steps"]
            )
            print(f"轮询 {index + 1}: {task['status']} | {steps}", flush=True)
            if task["status"] in {"completed", "failed", "completed_with_errors"}:
                break
            time.sleep(3)
        else:
            raise TimeoutError(f"任务 {task_id} 在 10 分钟内未结束")

        if task["status"] not in {"completed", "completed_with_errors"}:
            raise RuntimeError(f"工作流失败：{task}")

        result_page = checked(
            client.get("/api/match/results", params={"task_id": task_id})
        )
        print(f"匹配结果：{result_page['total']} 个", flush=True)

        reports = checked(client.get("/api/reports"))
        print(f"历史报告：{len(reports)} 份", flush=True)
        if reports:
            markdown = client.get(
                f"/api/reports/{reports[0]['id']}/markdown"
            ).text
            print(f"最新报告 Markdown：{len(markdown)} 字符", flush=True)


if __name__ == "__main__":
    main()
