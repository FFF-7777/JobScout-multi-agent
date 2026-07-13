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
  "Resume Agent": "📄",
  "Job Agent": "🏢",
  "Match Agent": "🎯",
  "Report Agent": "📝",
};
const agentDescs: Record<string, string> = {
  "Resume Agent": "解析简历，生成结构化候选人画像",
  "Job Agent": "抽取岗位技术栈、任职要求与风险标签",
  "Match Agent": "计算匹配度，输出 S/A/B/C/D 推荐等级",
  "Report Agent": "生成投递建议、面试题与打招呼话术",
};
const flow = [
  "上传固定简历",
  "解析简历画像",
  "导入岗位 JD / Excel",
  "解析岗位信息",
  "计算匹配度并排序",
  "生成投递与面试建议",
];

// LLM 探活结果（用户手动点测时刷新）
const pingBusy = ref(false);
const pingResult = ref<{ ok: boolean; message: string } | null>(null);

async function runPing() {
  pingBusy.value = true;
  pingResult.value = null;
  try {
    const r = await api.testLLM();
    pingResult.value = { ok: true, message: `已连通：${r.model} → ${r.reply}` };
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
    <div class="hero card">
      <h1>基于固定简历，帮你筛出最值得投的求职岗位</h1>
      <p>
        不是反复改简历，而是从大量岗位中筛出：哪些值得投、为什么值得投、哪里匹配、哪里有短板、面试会问什么、怎么介绍自己。
      </p>
      <div class="hero-actions">
        <el-button type="primary" size="large" @click="router.push('/resume')">开始分析</el-button>
        <el-tag v-if="health" :type="health.has_api_key ? 'success' : 'warning'" effect="dark">
          模型 {{ health.llm_model }} · {{ health.has_api_key ? "API 已配置" : "未配置 API Key" }}
        </el-tag>
        <el-tag v-else type="danger" effect="dark">后端未连接</el-tag>
        <el-button v-if="health?.has_api_key" size="default" :loading="pingBusy" @click="runPing">
          测试 LLM 连通
        </el-button>
      </div>
      <el-alert
        v-if="pingResult"
        :type="pingResult.ok ? 'success' : 'error'"
        :closable="true"
        @close="pingResult = null"
        :title="pingResult.message"
        show-icon
        style="margin-top: 12px"
      />
    </div>

    <div class="card">
      <div class="section-h">核心流程</div>
      <div class="flow">
        <template v-for="(f, i) in flow" :key="i">
          <div class="flow-node">{{ f }}</div>
          <div v-if="i < flow.length - 1" class="flow-arrow">→</div>
        </template>
      </div>
    </div>

    <div class="section-h" style="margin-left: 4px">四个核心 Agent</div>
    <div class="agents">
      <div v-for="(a, idx) in (health?.agents ?? [])" :key="a.name" class="card agent-card">
        <div class="agent-icon">{{ agentIcons[a.name] ?? "🤖" }}</div>
        <div class="agent-name">{{ a.name }}</div>
        <div class="agent-desc">{{ agentDescs[a.name] ?? "" }}</div>
        <div class="agent-meta">
          <el-tag size="small" :type="a.role === 'reasoning' ? 'warning' : 'info'" effect="plain">
            {{ a.role }}
          </el-tag>
          <el-tag
            v-if="a.enable_thinking"
            size="small"
            type="success"
            effect="plain"
            style="margin-left: 4px"
          >思考模式</el-tag>
        </div>
        <div class="agent-model" :title="`provider: ${a.provider}`">{{ a.model }}</div>
        <div class="agent-status">
          <span :class="['dot', a.configured ? 'dot-on' : 'dot-off']"></span>
          <span>{{ a.configured ? "已配置" : "未配置 API Key" }}</span>
        </div>
      </div>
      <div v-if="!health" class="card agent-card agent-card-empty">
        <div class="agent-icon">⚠️</div>
        <div class="agent-name">后端未连接</div>
        <div class="agent-desc">请检查服务是否运行于 http://127.0.0.1:8020</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hero h1 {
  font-size: 26px;
  margin: 0 0 12px;
}
.hero p {
  color: #5a6472;
  line-height: 1.7;
  max-width: 760px;
}
.hero-actions {
  margin-top: 18px;
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.flow {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.flow-node {
  background: #eef3ff;
  color: #3a6ff7;
  padding: 8px 14px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
}
.flow-arrow {
  color: #b8bfca;
}
.agents {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}
.agent-card {
  text-align: center;
  padding: 22px 16px 18px;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.agent-card-empty {
  grid-column: 1 / -1;
}
.agent-icon {
  font-size: 30px;
}
.agent-name {
  font-weight: 700;
  margin: 8px 0 4px;
}
.agent-desc {
  color: #8a94a6;
  font-size: 12px;
  line-height: 1.6;
  min-height: 38px;
}
.agent-meta {
  margin-top: 6px;
}
.agent-model {
  margin-top: 8px;
  font-size: 12px;
  color: #1f2733;
  font-weight: 600;
  word-break: break-all;
  max-width: 100%;
}
.agent-status {
  margin-top: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #5a6472;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.dot-on {
  background: #2fae5f;
  box-shadow: 0 0 0 3px rgba(47, 174, 95, 0.18);
}
.dot-off {
  background: #c0c4cc;
}
@media (max-width: 900px) {
  .agents {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
