# JobScout · AI 求职岗位筛选与匹配多智能体助手

> 基于固定简历，自动分析岗位 JD，筛选最值得投递的公司和具体岗位，并生成匹配度评分、投递优先级和面试准备建议。

JobScout 不是帮你反复改简历，而是帮你从大量岗位中筛出：**哪些岗位最值得投？为什么值得投？哪里匹配？哪里有短板？面试会问什么？投递时怎么介绍自己？**

技术栈：**FastAPI + LangGraph + 通义千问（阿里云百炼）+ Vue3 + Element Plus**。通过 4 个核心 Agent 编排完整工作流，保存每一步执行状态，实现多 Agent 任务流转与过程可视化。

---

## ✨ 核心能力

- 📄 **Resume Agent**：解析简历（PDF/DOCX/MD/TXT/粘贴文本）→ 结构化候选人画像（技能、项目、目标岗位、优劣势）
- 🏢 **Job Agent**：解析单个/批量 JD 及 Excel·CSV 岗位表 → 抽取技术栈、任职要求与风险标签（外包/培训/销售/运营/助教/不相关）
- 🎯 **Match Agent**：规则分 + LLM 五维分加权 → 匹配度评分与 S/A/B/C/D 推荐等级，支持批量排序
- 📝 **Report Agent**：生成投递建议、面试题预测、项目讲解重点、BOSS 打招呼话术与 HR 私信，支持导出 Markdown / Excel
- 🔗 **LangGraph 工作流**：`parse_resume → parse_jobs → match_jobs → generate_report`，每步状态落库并前端实时可视化

## 🖼️ 页面一览

| 页面 | 说明 |
| --- | --- |
| 首页 | 项目介绍、核心流程、4 Agent 卡片 |
| 简历画像 | 上传/粘贴简历，展示并可编辑技能/项目/目标岗位 |
| 岗位导入 | 单个/批量 JD、Excel·CSV 导入，待分析岗位列表 |
| **Agent 执行流程** | 4 个 Agent 实时状态 + 输出摘要（README 核心截图） |
| **岗位推荐结果** | 匹配度排序表格，城市/等级/技术栈筛选，导出 Excel |
| **岗位详情分析** | JD 原文、技术栈、匹配点、缺口、风险、面试题、话术 |
| 报告导出 | 历史报告、Markdown / Excel 导出 |

> 架构图与工作流图见 [`docs/architecture.md`](docs/architecture.md)。

## 🚀 快速开始

### 方式一：本地开发

**1. 后端**

```bash
cd server
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 填入你的百炼 API Key
uvicorn main:app --reload --port 8020
```

后端启动后：Swagger 文档 http://localhost:8020/docs ，健康检查 http://localhost:8020/health

**2. 前端**（`/api` 代理默认值已设为 `http://localhost:8020`，直接启动即可；如需指向其它端口，运行前设 `VITE_PROXY_TARGET` 环境变量）

```bash
cd app
npm install
npm run dev   # http://127.0.0.1:5173
```

> 端口说明：本项目默认后端 8000，本机 8000 已被其它进程占用，故演示用 8020，且 `app/vite.config.ts` 里 `proxyTarget` 默认值已改为 `http://localhost:8020`。若换到 8000 空闲的机器，把该默认值改回 `http://localhost:8000`、或启动前设置 `VITE_PROXY_TARGET=http://localhost:8000` 即可。

### 方式二：Docker Compose 一键启动

```bash
export DASHSCOPE_API_KEY=sk-你的Key       # Windows PowerShell: $env:DASHSCOPE_API_KEY="sk-..."
docker compose up --build
```

- 前端：http://localhost:8080
- 后端：http://localhost:8000/docs

## 🔑 环境变量（server/.env）

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | 阿里云百炼 API Key（**必填**） | — |
| `LLM_BASE_URL` | OpenAI 兼容端点 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `LLM_MODEL` | 模型名称 | `qwen-plus`（可切 `qwen-max` / `qwen3.7-plus`） |
| `DATABASE_URL` | 数据库连接 | `sqlite:///./internscout.db` |
| `CORS_ORIGINS` | 允许跨域来源 | `*` |

> 获取 Key：登录 [阿里云百炼控制台](https://bailian.console.aliyun.com/) → API-KEY → 创建。本项目为纯 LLM 实现，未配置 Key 时相关接口会明确报错。

## 📚 主要接口

```text
# 简历
POST /api/resumes/upload | /api/resumes/parse
GET  /api/resumes[/{id}]        PUT /api/resumes/{id}/profile
# 岗位
POST /api/jobs/import-text | /api/jobs/import-file
GET  /api/jobs[/{id}]           POST /api/jobs/{id}/analyze
# 匹配
GET  /api/match/results[/{id}]
# Agent 工作流
POST /api/agents/run            GET /api/agents/tasks/{task_id}[/steps]
# 报告
GET  /api/reports[/{id}]        GET /api/reports/{id}/markdown | /excel
# 系统
GET  /health                    POST /api/test-llm
```

## 🧪 示例数据

`server/sample_data/`：
- `sample_resume.md`：大三 AI 专业示例简历（羽智选 RAG 项目）
- `sample_jd.txt`：AI Agent 应用开发实习生 JD
- `sample_jobs.csv` / `sample_jobs.xlsx`：5 条含高/中/低匹配与风险岗位的示例表格

## 🗂️ 目录结构

```text
internscout/
├── server/           # FastAPI + LangGraph 后端
│   ├── models/ schemas/ routers/ services/ prompts/ sample_data/
│   ├── main.py config.py database.py requirements.txt .env.example
├── app/              # Vue3 + Vite + Element Plus 前端
│   └── src/{router,api,stores,views,components}
├── docs/             # 架构图与工作流说明
├── Dockerfile.server Dockerfile.app docker-compose.yml
└── README.md
```

## 📌 最小可运行版本

上传简历 → 导入岗位 → 一键分析 → 自动执行 4 Agent（过程可视）→ 输出简历画像、岗位结构化分析、匹配评分、推荐等级、投递建议与面试准备。
