"""端到端验证：真实调用 4 Agent，验证主流程、多岗解析与报告导出。"""
import httpx, time, os

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8020")
SAMPLE = r"d:\AI+\JobScout AI 求职岗位筛选与匹配多智能体助手\server\sample_data"
client = httpx.Client(base_url=BASE, timeout=180)

# 1) 解析示例简历
with open(os.path.join(SAMPLE, "sample_resume.md"), encoding="utf-8") as f:
    resume_text = f.read()
r = client.post("/api/resumes/parse", json={"text": resume_text, "filename": "sample_resume.md"})
print("parse resume:", r.status_code, "id=", r.json().get("id"), flush=True)
resume_id = r.json()["id"]

# 2) 导入多岗位（CSV，验证多岗解析与隔离）
with open(os.path.join(SAMPLE, "sample_jobs.csv"), "rb") as f:
    csv_bytes = f.read()
r = client.post("/api/jobs/import-file", files={"file": ("sample_jobs.csv", csv_bytes, "text/csv")})
print("import jobs:", r.status_code, "count=", len(r.json()), flush=True)
job_ids = [j["id"] for j in r.json()]

# 3) 启动工作流
r = client.post("/api/agents/run", json={"resume_id": resume_id, "job_ids": job_ids})
t0 = r.json()
task_id = t0["task_id"]
print("run task:", task_id, "status:", t0["status"], flush=True)

# 4) 轮询进度
for i in range(200):
    t = client.get(f"/api/agents/tasks/{task_id}").json()
    line = "  ".join(
        f'{s["agent_name"]}:{s["status"]}({s.get("progress", 0)})' for s in t["steps"]
    )
    print(f"poll[{i}] {t['status']} | {line}", flush=True)
    if t["status"] in ("completed", "failed", "completed_with_errors"):
        break
    time.sleep(3)

# 5) 匹配结果
res = client.get("/api/match/results", params={"task_id": task_id}).json()
print(f"match results: {len(res)}", flush=True)
for x in res[:5]:
    print(f"  {x['company_name']} / {x['job_title']} => {x['score']} ({x['level']})", flush=True)

# 6) 报告
reps = client.get("/api/reports").json()
print(f"reports: {len(reps)}", flush=True)
if reps:
    print("  latest report:", reps[0]["id"], reps[0]["title"], flush=True)
    md = client.get(f"/api/reports/{reps[0]['id']}/markdown").text
    print("  markdown length:", len(md), flush=True)

print("E2E DONE", flush=True)
