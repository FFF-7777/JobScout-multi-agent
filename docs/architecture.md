# 系统架构与工作流

## 总体架构

```text
┌─────────────────────────────────────────────────────────┐
│                     前端 Vue3 + Element Plus              │
│  首页 / 简历画像 / 岗位导入 / Agent执行 / 结果 / 详情 / 报告 │
└───────────────────────────┬─────────────────────────────┘
                            │  Axios (REST /api)
┌───────────────────────────▼─────────────────────────────┐
│                      FastAPI 后端                         │
│  routers: resumes / jobs / match / agents / reports      │
│  ┌────────────────────────────────────────────────────┐ │
│  │           LangGraph 工作流 (workflow.py)            │ │
│  │  parse_resume → parse_jobs → match_jobs → report   │ │
│  └───────┬───────────┬────────────┬──────────┬────────┘ │
│  Resume Agent   Job Agent    Match Agent  Report Agent   │
│          │           │            │          │           │
│  ┌───────▼───────────▼────────────▼──────────▼────────┐ │
│  │  llm_service (阿里云百炼 / 通义千问 OpenAI 兼容)     │ │
│  └────────────────────────────────────────────────────┘ │
│  SQLAlchemy ORM ── SQLite (6 表)                         │
└─────────────────────────────────────────────────────────┘
```

## LangGraph 工作流

```text
        ┌──────────────┐
 START →│ parse_resume │  Resume Agent：简历文本 → 候选人画像
        └──────┬───────┘
               ▼
        ┌──────────────┐
        │  parse_jobs  │  Job Agent：JD → 岗位画像（技术栈/风险标签）
        └──────┬───────┘
               ▼
        ┌──────────────┐
        │  match_jobs  │  Match Agent：规则分 + LLM 五维分 → 匹配度/等级
        └──────┬───────┘
               ▼
        ┌──────────────┐
        │generate_report│ Report Agent：投递建议/面试题/话术 → Markdown
        └──────┬───────┘
               ▼
              END
```

每个节点执行前后写入 `agent_runs` 表（pending → running → success/failed），
前端通过 `GET /api/agents/tasks/{task_id}` 轮询实现过程可视化。

## 匹配评分规则

| 维度 | 权重 | 来源 |
| --- | --: | --- |
| 技术栈匹配 | 30% | 规则分(技术栈交集覆盖率)×0.6 + LLM 分×0.4 |
| 项目经验匹配 | 30% | LLM |
| 岗位方向匹配 | 20% | LLM |
| 学历/年级/求职条件 | 10% | LLM |
| 城市/薪资/可投递性 | 10% | LLM |

推荐等级：90-100 = S，80-89 = A，70-79 = B，55-69 = C，0-54 = D。

## 数据表

`resumes` / `jobs` / `job_analysis` / `match_results` / `agent_runs` / `reports`
