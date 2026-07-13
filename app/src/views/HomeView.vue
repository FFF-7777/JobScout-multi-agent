<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, type AgentHealth, type LLMTestItem } from "@/api";

const router = useRouter();
const health = ref<{
  status: string;
  llm_model: string;
  has_api_key: boolean;
  llm_base_url?: string;
  llm_timeout?: number;
  agents: AgentHealth[];
} | null>(null);

const agentIcons: Record<string, string> = {
  "Resume Agent": "RA",
  "Job Agent": "JA",
  "Match Agent": "MA",
  "Report Agent": "RP",
};
const agentLabels: Record<string, string> = {
  "Resume Agent": "简历智能体",
  "Job Agent": "岗位智能体",
  "Match Agent": "匹配智能体",
  "Match Agent (Quick)": "匹配智能体（快速）",
  "Match Agent (Deep)": "匹配智能体（深度）",
  "Report Agent": "报告智能体",
};
const agentDescs: Record<string, string> = {
  "Resume Agent": "提取技能、项目与目标岗位，生成稳定候选人画像。",
  "Job Agent": "结构化解析 JD，识别要求、风险标签与优先条件。",
  "Match Agent": "综合简历与岗位信号，输出分数、等级与行动建议。",
  "Report Agent": "生成投递策略、面试准备与可直接使用的沟通内容。",
};
const flow = [
  "上传固定简历",
  "生成候选人画像",
  "导入岗位 JD / 表格",
  "解析岗位要求",
  "计算匹配结果",
  "生成报告与面试建议",
];

const pingBusy = ref(false);
const pingResult = ref<{ ok: boolean; results: LLMTestItem[] } | null>(null);

type InlinePingStatus = {
  state: "idle" | "ok" | "fail";
  text: string;
  title?: string;
};

function simplifyPingMessage(reply: string) {
  const message = String(reply || "").replace(/^已连通[:：]?\s*/u, "").trim();
  if (!message) return "连接异常";
  if (message === "可用") return "正常";
  if (message.startsWith("不可用：")) return message.slice(4).trim() || "连接异常";
  return message;
}

function getPingCandidates(agentName: string) {
  if (agentName === "Match Agent") {
    return ["Match Agent", "Match Agent (Quick)", "Match Agent (Deep)"];
  }
  return [agentName];
}

function getAgentPingStatus(agentName: string): InlinePingStatus {
  const results = pingResult.value?.results ?? [];
  const related = results.filter((item) => getPingCandidates(agentName).includes(item.name));
  if (!related.length) {
    return { state: "idle", text: "待测试" };
  }

  const failed = related.find((item) => !item.ok);
  if (failed) {
    const message = simplifyPingMessage(failed.reply);
    return { state: "fail", text: message, title: message };
  }

  return { state: "ok", text: "正常" };
}

async function runPing() {
  pingBusy.value = true;
  pingResult.value = null;
  try {
    const r = await api.testLLM();
    pingResult.value = { ok: r.ok, results: r.results };
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e?.message || "调用失败";
    pingResult.value = {
      ok: false,
      results: [{ name: "系统", ok: false, model: "-", reply: `不可用：${detail}` }],
    };
  } finally {
    pingBusy.value = false;
  }
}

onMounted(async () => {
  try {
    health.value = await api.health();
  } catch {
    health.value = null;
  }
});
</script>

<template>
  <div class="page home-page">
    <section class="hero-grid">
      <div class="card hero-card">
        <span class="eyebrow">React Bits 风格升级中</span>
        <h1>从海量岗位里，快速筛出真正值得投的机会。</h1>
        <p>
          这不是一个只会打分的工具页，而是一条完整的求职决策链路：先理解你的履历，再理解岗位，再给出匹配深度、投递优先级和后续动作。
        </p>
        <div class="hero-actions">
          <el-button
            type="primary"
            size="large"
            class="hero-primary-btn"
            @click="router.push('/resume')"
          >
            开始分析
          </el-button>
          <el-button plain size="large" class="hero-secondary-btn" @click="router.push('/results')">
            查看结果
          </el-button>
        </div>
        <div class="hero-flow">
          <div v-for="(item, index) in flow" :key="item" class="flow-item">
            <span class="flow-index">{{ index + 1 }}</span>
            <span>{{ item }}</span>
          </div>
        </div>
      </div>

      <div class="card hero-side">
        <div class="health-row">
          <div>
            <div class="mini-label">模型状态</div>
            <div class="health-title">{{ health ? "多模型已接入" : "等待连接" }}</div>
          </div>
          <el-tag v-if="health" :type="health.has_api_key ? 'success' : 'warning'">
            {{ health.has_api_key ? "API 已配置" : "缺少 API Key" }}
          </el-tag>
          <el-tag v-else type="danger">后端未连接</el-tag>
        </div>
        <div v-if="health?.agents?.length" class="model-list">
          <div v-for="agent in health.agents" :key="agent.name" class="model-item">
            <div class="model-agent">
              <div class="model-agent-main">
                <span>{{ agentLabels[agent.name] || agent.name }}</span>
                <span
                  :class="['inline-ping-status', `is-${getAgentPingStatus(agent.name).state}`]"
                  :title="getAgentPingStatus(agent.name).title || getAgentPingStatus(agent.name).text"
                >
                  <span class="inline-ping-dot"></span>
                  <span class="inline-ping-text">{{ getAgentPingStatus(agent.name).text }}</span>
                </span>
              </div>
              <el-tag size="small" effect="plain" :type="agent.enable_thinking ? 'success' : 'info'">
                {{ agent.role }}
              </el-tag>
            </div>
            <div class="model-name">{{ agent.model }}</div>
          </div>
        </div>
        <div class="health-grid">
          <div class="health-metric">
            <span>Agent 数量</span>
            <b>{{ health?.agents?.length ?? 0 }}</b>
          </div>
          <div class="health-metric">
            <span>连接状态</span>
            <b>{{ health ? "正常" : "异常" }}</b>
          </div>
        </div>
        <div class="health-actions">
          <el-button v-if="health?.has_api_key" :loading="pingBusy" @click="runPing">
            测试 LLM 连通性
          </el-button>
          <div v-if="pingResult" class="ping-summary-inline" :class="pingResult.ok ? 'ok' : 'fail'">
            {{ pingResult.ok ? "连通性测试已完成" : "存在未连通模型" }}
          </div>
        </div>
      </div>
    </section>

    <section class="agent-section">
      <div class="agent-heading">
        <div>
          <div class="section-h agent-title">四个核心 Agent</div>
          <div class="agent-sub">四个 Agent 分工清晰，首页首屏直接展示。</div>
        </div>
      </div>

      <div class="agents">
        <div v-for="a in health?.agents ?? []" :key="a.name" class="card agent-card">
          <div class="agent-badge">{{ agentIcons[a.name] ?? "AI" }}</div>
          <div class="agent-name">{{ agentLabels[a.name] ?? a.name }}</div>
          <div class="agent-desc">{{ agentDescs[a.name] ?? "" }}</div>
          <div class="agent-tags">
            <el-tag size="small" :type="a.role === 'reasoning' ? 'warning' : 'info'" effect="plain">
              {{ a.role }}
            </el-tag>
            <el-tag v-if="a.enable_thinking" size="small" type="success" effect="plain">Thinking</el-tag>
          </div>
          <div class="agent-model">{{ a.model }}</div>
          <div class="agent-status">
            <span :class="['status-dot', a.configured ? 'on' : 'off']"></span>
            <span>{{ a.configured ? "已配置" : "未配置" }}</span>
          </div>
        </div>

        <div v-if="!health" class="card agent-card empty-card">
          <div class="agent-badge muted">NA</div>
          <div class="agent-name">后端未连接</div>
          <div class="agent-desc">请检查服务是否运行在 `http://127.0.0.1:8020`，连接恢复后这里会自动显示 Agent 状态。</div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.home-page {
  min-height: calc(100vh - 112px);
  padding-top: 22px;
  padding-bottom: 28px;
  display: flex;
  flex-direction: column;
}

.hero-card {
  padding: 24px 26px;
  height: 100%;
}

.hero-card h1 {
  margin: 12px 0 10px;
  color: #112038;
  font-size: 34px;
  line-height: 1.12;
  letter-spacing: -0.04em;
}

.hero-card p {
  max-width: 760px;
  color: #5d6a84;
  font-size: 14px;
  line-height: 1.75;
}

.hero-actions {
  display: flex;
  gap: 12px;
  margin-top: 18px;
  flex-wrap: wrap;
}

.hero-primary-btn {
  min-width: 160px;
  height: 50px;
  padding: 0 24px;
  border: 1px solid rgba(84, 126, 255, 0.96);
  border-radius: 18px;
  background: linear-gradient(135deg, #74aeff 0%, #618eff 42%, #6a63ff 100%) !important;
  box-shadow:
    0 14px 34px rgba(92, 117, 255, 0.26),
    0 0 0 5px rgba(115, 156, 255, 0.12);
  font-weight: 800;
  letter-spacing: 0.01em;
  transition: transform 0.22s ease, box-shadow 0.22s ease, filter 0.22s ease;
  animation: heroCtaPulse 1.8s ease-in-out infinite;
}

.hero-primary-btn:hover {
  transform: translateY(-2px) scale(1.015);
  filter: brightness(1.03);
  box-shadow:
    0 18px 38px rgba(92, 117, 255, 0.32),
    0 0 0 7px rgba(115, 156, 255, 0.14);
}

.hero-secondary-btn {
  min-width: 120px;
  height: 50px;
  padding: 0 22px;
  border-radius: 18px;
  border-color: rgba(188, 199, 221, 0.72);
  background: rgba(255, 255, 255, 0.74);
  color: #42506a;
  font-weight: 650;
  transition: all 0.22s ease;
}

.hero-secondary-btn:hover {
  border-color: rgba(120, 145, 220, 0.54);
  color: #2e426c;
  background: rgba(255, 255, 255, 0.92);
}

@keyframes heroCtaPulse {
  0%, 100% {
    box-shadow:
      0 14px 34px rgba(92, 117, 255, 0.26),
      0 0 0 5px rgba(115, 156, 255, 0.12);
  }
  50% {
    box-shadow:
      0 18px 40px rgba(92, 117, 255, 0.34),
      0 0 0 9px rgba(115, 156, 255, 0.1);
  }
}

.hero-flow {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-top: 20px;
}

.flow-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.68);
  border: 1px solid rgba(169, 184, 214, 0.22);
  color: #354564;
  font-size: 13px;
  min-height: 52px;
}

.flow-index {
  width: 24px;
  height: 24px;
  border-radius: 999px;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, rgba(106, 140, 255, 0.18), rgba(143, 211, 255, 0.42));
  color: #4563f3;
  font-size: 12px;
  font-weight: 700;
}

.hero-side {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 100%;
  height: 100%;
  padding: 20px 22px;
}

.health-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.mini-label {
  color: #77839b;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.health-title {
  margin-top: 6px;
  color: #16253d;
  font-size: 18px;
  font-weight: 760;
  line-height: 1.2;
  word-break: break-word;
}

.health-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}

.model-list {
  display: grid;
  gap: 8px;
}

.model-item {
  padding: 10px 12px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(169, 184, 214, 0.22);
}

.model-agent {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  color: #33415d;
  font-size: 12px;
  font-weight: 700;
}

.model-agent-main {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.model-name {
  margin-top: 6px;
  color: #1b2942;
  font-size: 13px;
  font-weight: 700;
  line-height: 1.4;
  word-break: break-word;
}

.model-meta {
  margin-top: 4px;
  color: #7a8599;
  font-size: 11px;
}

.health-metric {
  padding: 12px 14px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.68);
  border: 1px solid rgba(169, 184, 214, 0.22);
}

.health-metric span {
  display: block;
  color: #79859d;
  font-size: 12px;
}

.health-metric b {
  display: block;
  margin-top: 4px;
  color: #1b2942;
  font-size: 16px;
}

.health-actions {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}

.inline-ping-status {
  min-width: 0;
  max-width: 100%;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 1px 0;
  color: #7c889e;
  flex-shrink: 1;
}

.inline-ping-dot {
  width: 8px;
  height: 8px;
  flex: 0 0 8px;
  border-radius: 999px;
  border: 1px solid rgba(180, 191, 209, 0.9);
  background: #ffffff;
  box-shadow: 0 0 0 4px rgba(227, 233, 244, 0.55);
}

.inline-ping-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  line-height: 1.2;
}

.inline-ping-status.is-ok {
  color: #2a9651;
}

.inline-ping-status.is-ok .inline-ping-dot {
  border-color: rgba(55, 181, 95, 0.95);
  background: #34c759;
  box-shadow: 0 0 0 4px rgba(52, 199, 89, 0.14);
}

.inline-ping-status.is-fail {
  color: #d04646;
}

.inline-ping-status.is-fail .inline-ping-dot {
  border-color: rgba(220, 79, 79, 0.95);
  background: #ff5f57;
  box-shadow: 0 0 0 4px rgba(255, 95, 87, 0.14);
}

.ping-summary-inline {
  max-width: 100%;
  min-width: 0;
  padding: 8px 12px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 1.4;
  border: 1px solid rgba(169, 184, 214, 0.22);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ping-summary-inline.ok {
  color: #256a3f;
  background: rgba(235, 248, 235, 0.88);
  border-color: rgba(125, 199, 131, 0.3);
}

.ping-summary-inline.fail {
  color: #b34545;
  background: rgba(255, 238, 238, 0.9);
  border-color: rgba(235, 138, 138, 0.3);
}

.agent-section {
  margin-top: 8px;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.agent-heading {
  display: flex;
  justify-content: space-between;
  align-items: end;
  gap: 16px;
  margin-bottom: 10px;
}

.agent-title {
  margin: 0;
  font-size: 18px;
}

.agent-sub {
  margin-top: 4px;
  color: #6f7d95;
  font-size: 12px;
}

.agents {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.agent-card {
  text-align: left;
  padding: 16px 16px 14px;
  min-height: 196px;
}

.agent-badge {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 14px;
  background: linear-gradient(135deg, rgba(110, 152, 255, 0.18), rgba(143, 211, 255, 0.36));
  color: #4061ef;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.04em;
}

.agent-badge.muted {
  color: #7f8aa4;
  background: rgba(236, 240, 247, 0.94);
}

.agent-name {
  margin-top: 12px;
  color: #16253c;
  font-size: 16px;
  font-weight: 720;
}

.agent-desc {
  min-height: 44px;
  margin-top: 8px;
  color: #6a7690;
  font-size: 12px;
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.agent-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 8px;
}

.agent-model {
  margin-top: 10px;
  color: #2f3d59;
  font-size: 12px;
  font-weight: 600;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.agent-status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  color: #60708c;
  font-size: 12px;
}

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
}

.status-dot.on {
  background: #2fae5f;
  box-shadow: 0 0 0 6px rgba(47, 174, 95, 0.12);
}

.status-dot.off {
  background: #b3bdcf;
  box-shadow: 0 0 0 6px rgba(179, 189, 207, 0.16);
}

.empty-card {
  grid-column: 1 / -1;
}

@media (max-width: 1100px) {
  .hero-flow {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .agents {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .hero-card h1 {
    font-size: 34px;
  }

  .home-page {
    min-height: auto;
  }

  .hero-flow {
    grid-template-columns: 1fr;
  }

  .health-grid,
  .agents {
    grid-template-columns: 1fr;
  }
}
</style>
