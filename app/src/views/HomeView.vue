<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, type AgentHealth } from "@/api";

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
const pingResult = ref<{ ok: boolean; message: string } | null>(null);

async function runPing() {
  pingBusy.value = true;
  pingResult.value = null;
  try {
    const r = await api.testLLM();
    pingResult.value = { ok: true, message: `已连通：${r.model} · ${r.reply}` };
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e?.message || "调用失败";
    pingResult.value = { ok: false, message: `不可用：${detail}` };
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
  <div class="page">
    <section class="hero-grid">
      <div class="card hero-card">
        <span class="eyebrow">React Bits 风格升级中</span>
        <h1>从海量岗位里，快速筛出真正值得投的机会。</h1>
        <p>
          这不是一个只会打分的工具页，而是一条完整的求职决策链路：先理解你的履历，再理解岗位，再给出匹配深度、投递优先级和后续动作。
        </p>
        <div class="hero-actions">
          <el-button type="primary" size="large" @click="router.push('/resume')">开始分析</el-button>
          <el-button plain size="large" @click="router.push('/results')">查看结果</el-button>
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
            <div class="health-title">{{ health?.llm_model || "等待连接" }}</div>
          </div>
          <el-tag v-if="health" :type="health.has_api_key ? 'success' : 'warning'">
            {{ health.has_api_key ? "API 已配置" : "缺少 API Key" }}
          </el-tag>
          <el-tag v-else type="danger">后端未连接</el-tag>
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
        </div>
        <el-alert
          v-if="pingResult"
          :type="pingResult.ok ? 'success' : 'error'"
          :closable="true"
          :title="pingResult.message"
          @close="pingResult = null"
          show-icon
        />
      </div>
    </section>

    <section class="agent-section">
      <div class="agent-heading">
        <div>
          <div class="section-h agent-title">四个核心 Agent</div>
          <div class="agent-sub">统一使用浅色玻璃卡片和更清晰的信息层级，避免“功能有了但质感很弱”的问题。</div>
        </div>
      </div>

      <div class="agents">
        <div v-for="a in health?.agents ?? []" :key="a.name" class="card agent-card">
          <div class="agent-badge">{{ agentIcons[a.name] ?? "AI" }}</div>
          <div class="agent-name">{{ a.name }}</div>
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
.hero-card {
  padding: 28px;
}

.hero-card h1 {
  margin: 16px 0 14px;
  color: #112038;
  font-size: 42px;
  line-height: 1.08;
  letter-spacing: -0.04em;
}

.hero-card p {
  max-width: 760px;
  color: #5d6a84;
  font-size: 15px;
}

.hero-actions {
  display: flex;
  gap: 12px;
  margin-top: 24px;
  flex-wrap: wrap;
}

.hero-flow {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 28px;
}

.flow-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.68);
  border: 1px solid rgba(169, 184, 214, 0.22);
  color: #354564;
}

.flow-index {
  width: 28px;
  height: 28px;
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
  gap: 16px;
  min-height: 100%;
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
  margin-top: 8px;
  color: #16253d;
  font-size: 24px;
  font-weight: 760;
  line-height: 1.2;
}

.health-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

.health-metric {
  padding: 16px;
  border-radius: 18px;
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
  margin-top: 6px;
  color: #1b2942;
  font-size: 20px;
}

.health-actions {
  display: flex;
  gap: 12px;
}

.agent-section {
  margin-top: 26px;
}

.agent-heading {
  display: flex;
  justify-content: space-between;
  align-items: end;
  gap: 16px;
  margin-bottom: 14px;
}

.agent-title {
  margin: 0;
  font-size: 24px;
}

.agent-sub {
  margin-top: 8px;
  color: #6f7d95;
  font-size: 14px;
}

.agents {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

.agent-card {
  text-align: left;
  padding: 22px;
}

.agent-badge {
  width: 46px;
  height: 46px;
  display: grid;
  place-items: center;
  border-radius: 16px;
  background: linear-gradient(135deg, rgba(110, 152, 255, 0.18), rgba(143, 211, 255, 0.36));
  color: #4061ef;
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.04em;
}

.agent-badge.muted {
  color: #7f8aa4;
  background: rgba(236, 240, 247, 0.94);
}

.agent-name {
  margin-top: 18px;
  color: #16253c;
  font-size: 18px;
  font-weight: 720;
}

.agent-desc {
  min-height: 72px;
  margin-top: 10px;
  color: #6a7690;
  font-size: 13px;
  line-height: 1.75;
}

.agent-tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.agent-model {
  margin-top: 12px;
  color: #2f3d59;
  font-size: 13px;
  font-weight: 600;
  word-break: break-word;
}

.agent-status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
  color: #60708c;
  font-size: 13px;
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
  .agents {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .hero-card h1 {
    font-size: 34px;
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
