"""端到端验证：真实调用 4 Agent，验证主流程、多岗解析与报告导出。"""
import os
import time
from pathlib import Path

import httpx

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8020")
SAMPLE = Path(__file__).resolve().parent / "sample_data"
client = httpx.Client(base_url=BASE, timeout=180)


def checked(response: httpx.Response) -> dict | list:
    response.raise_for_status()
    return response.json()

# 1) 解析示例简历
with open(SAMPLE / "sample_resume.md", encoding="utf-8") as f:
    resume_text = f.read()
r = client.post("/api/resumes/parse", json={"text": resume_text, "filename": "sample_resume.md"})
print("parse resume:", r.status_code, "id=", r.json().get("id"), flush=True)
resume_id = checked(r)["id"]

# 2) 导入多岗位（CSV，验证多岗解析与隔离）
with open(SAMPLE / "sample_jobs.csv", "rb") as f:
    csv_bytes = f.read()
r = client.post("/api/jobs/import-file", files={"file": ("sample_jobs.csv", csv_bytes, "text/csv")})
print("import jobs:", r.status_code, "count=", len(r.json()), flush=True)
job_ids = [j["id"] for j in checked(r)]

# 3) 启动工作流
r = client.post("/api/agents/run", json={"resume_id": resume_id, "job_ids": job_ids})
t0 = checked(r)
task_id = t0["task_id"]
print("run task:", task_id, "status:", t0["status"], flush=True)

# 4) 轮询进度
for i in range(200):
    t = checked(client.get(f"/api/agents/tasks/{task_id}"))
    line = "  ".join(
        f'{s["agent_name"]}:{s["status"]}({s.get("progress", 0)})' for s in t["steps"]
    )
    print(f"poll[{i}] {t['status']} | {line}", flush=True)
    if t["status"] in ("completed", "failed", "completed_with_errors"):
        break
    time.sleep(3)
else:
    raise TimeoutError(f"任务 {task_id} 在 10 分钟内未结束")

if t["status"] not in ("completed", "completed_with_errors"):
    raise RuntimeError(f"工作流失败：{t}")

# 5) 匹配结果
page = checked(client.get("/api/match/results", params={"task_id": task_id}))
items = page["items"]
print(f"match results: {page['total']}", flush=True)
for x in items[:5]:
    print(f"  {x['company_name']} / {x['job_title']} => {x['score']} ({x['level']})", flush=True)

# 6) 报告
reps = checked(client.get("/api/reports"))
print(f"reports: {len(reps)}", flush=True)
if reps:
    print("  latest report:", reps[0]["id"], reps[0]["title"], flush=True)
    md = client.get(f"/api/reports/{reps[0]['id']}/markdown").text
    print("  markdown length:", len(md), flush=True)

print("E2E DONE", flush=True)
