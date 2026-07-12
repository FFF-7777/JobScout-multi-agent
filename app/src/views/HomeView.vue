<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "@/api";

const router = useRouter();
const health = ref<{ status: string; llm_model: string; has_api_key: boolean } | null>(null);

const agents = [
  { name: "Resume Agent", desc: "解析简历，生成结构化候选人画像", icon: "📄" },
  { name: "Job Agent", desc: "抽取岗位技术栈、任职要求与风险标签", icon: "🏢" },
  { name: "Match Agent", desc: "计算匹配度，输出 S/A/B/C/D 推荐等级", icon: "🎯" },
  { name: "Report Agent", desc: "生成投递建议、面试题与打招呼话术", icon: "📝" },
];
const flow = [
  "上传固定简历",
  "解析简历画像",
  "导入岗位 JD / Excel",
  "解析岗位信息",
  "计算匹配度并排序",
  "生成投递与面试建议",
];

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
      </div>
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
      <div v-for="a in agents" :key="a.name" class="card agent-card">
        <div class="agent-icon">{{ a.icon }}</div>
        <div class="agent-name">{{ a.name }}</div>
        <div class="agent-desc">{{ a.desc }}</div>
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
}
.agent-icon {
  font-size: 30px;
}
.agent-name {
  font-weight: 700;
  margin: 8px 0 6px;
}
.agent-desc {
  color: #8a94a6;
  font-size: 13px;
  line-height: 1.6;
}
@media (max-width: 900px) {
  .agents {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
