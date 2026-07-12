<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, type Job, type MatchResult } from "@/api";
import { useAppStore } from "@/stores/app";

const route = useRoute();
const router = useRouter();
const store = useAppStore();

const rawId = Number(route.params.id);
const jobId = Number.isFinite(rawId) && rawId > 0 ? rawId : null;
const job = ref<Job | null>(null);
const match = ref<MatchResult | null>(null);
const loading = ref(true);

const report = computed(() => match.value?.detail_json?.report ?? null);
const dims = computed(() => match.value?.detail_json?.dimensions ?? null);

const DIM_LABELS: Record<string, string> = {
  tech_stack: "技术栈匹配",
  project_exp: "项目经验",
  role_direction: "岗位方向",
  qualification: "学历/求职",
  logistics: "城市/薪资",
};

async function load() {
  if (jobId === null) {
    ElMessage.error("无效的岗位 ID");
    router.replace("/jobs");
    return;
  }
  loading.value = true;
  try {
    job.value = await api.getJob(jobId);
    const results = await api.listResults(store.taskId || undefined);
    match.value = results.find((r) => r.job_id === jobId) || null;
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "加载失败");
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="page" v-loading="loading">
    <el-button link type="primary" @click="router.back()">← 返回</el-button>
    <div v-if="job">
      <div class="page-title">{{ job.company_name || "岗位" }} · {{ job.job_title }}</div>
      <div class="page-sub">{{ job.city }} · {{ job.salary }} · 来源 {{ job.source }}</div>

      <div v-if="match" class="card score-card">
        <div class="score-main">
          <span :class="['grade-tag', 'grade-' + match.level]" style="font-size: 22px; padding: 6px 16px">
            {{ match.level }}
          </span>
          <div>
            <div class="score-num">{{ match.score }} 分</div>
            <div class="score-rec">{{ match.recommendation }}</div>
          </div>
        </div>
        <div v-if="dims" class="dims">
          <div v-for="(v, k) in dims" :key="k" class="dim">
            <div class="dim-label">{{ DIM_LABELS[k] || k }}</div>
            <el-progress type="dashboard" :percentage="Math.round(Number(v))" :width="72" />
          </div>
        </div>
      </div>

      <div class="grid2">
        <div class="card">
          <div class="section-h">✅ 匹配点</div>
          <ul><li v-for="p in match?.matched_points" :key="p">{{ p }}</li></ul>
        </div>
        <div class="card">
          <div class="section-h">⚠️ 缺口分析</div>
          <ul><li v-for="p in match?.missing_points" :key="p">{{ p }}</li></ul>
        </div>
      </div>

      <div v-if="match?.risk_notes?.length || report?.risks?.length" class="card">
        <div class="section-h">🚩 风险提示</div>
        <ul>
          <li v-for="p in [...(match?.risk_notes || []), ...(report?.risks || [])]" :key="p">{{ p }}</li>
        </ul>
      </div>

      <div v-if="report" class="card">
        <div class="section-h">📌 投递建议</div>
        <p><b>{{ report.conclusion }}</b> — {{ report.priority }}</p>
        <ul><li v-for="r in report.reasons" :key="r">{{ r }}</li></ul>
      </div>

      <div v-if="report" class="grid2">
        <div class="card">
          <div class="section-h">💬 面试可能问题</div>
          <ol><li v-for="q in report.interview_questions" :key="q">{{ q }}</li></ol>
        </div>
        <div class="card">
          <div class="section-h">🎤 项目讲解重点</div>
          <ul><li v-for="q in report.project_talking_points" :key="q">{{ q }}</li></ul>
        </div>
      </div>

      <div v-if="report" class="grid2">
        <div class="card">
          <div class="section-h">👋 BOSS 打招呼话术</div>
          <div class="quote">{{ report.boss_greeting }}</div>
        </div>
        <div class="card">
          <div class="section-h">✉️ HR 私信</div>
          <div class="quote">{{ report.hr_message }}</div>
        </div>
      </div>

      <div class="card">
        <div class="section-h">📋 岗位解析</div>
        <div class="chips">
          <span class="chip-title">必备技能</span>
          <el-tag v-for="s in job.analysis?.required_skills" :key="s" type="danger" effect="plain" style="margin: 3px">{{ s }}</el-tag>
        </div>
        <div class="chips">
          <span class="chip-title">加分技能</span>
          <el-tag v-for="s in job.analysis?.preferred_skills" :key="s" style="margin: 3px">{{ s }}</el-tag>
        </div>
        <div class="chips" v-if="job.analysis?.risk_tags?.length">
          <span class="chip-title">风险标签</span>
          <el-tag v-for="s in job.analysis?.risk_tags" :key="s" type="warning" style="margin: 3px">{{ s }}</el-tag>
        </div>
      </div>

      <div class="card">
        <div class="section-h">📄 JD 原文</div>
        <pre class="jd">{{ job.jd_text }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
.score-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 20px;
}
.score-main {
  display: flex;
  align-items: center;
  gap: 16px;
}
.score-num {
  font-size: 26px;
  font-weight: 800;
}
.score-rec {
  color: #5a6472;
}
.dims {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.dim {
  text-align: center;
}
.dim-label {
  font-size: 12px;
  color: #8a94a6;
  margin-bottom: 2px;
}
.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
}
.grid2 .card {
  margin-bottom: 0;
}
.grid2 {
  margin-bottom: 18px;
}
ul,
ol {
  margin: 4px 0;
  padding-left: 20px;
  line-height: 1.8;
}
.quote {
  background: #f7f9ff;
  border-left: 3px solid #3a6ff7;
  padding: 12px 14px;
  border-radius: 6px;
  line-height: 1.7;
}
.chips {
  margin: 6px 0;
}
.chip-title {
  color: #8a94a6;
  font-size: 13px;
  margin-right: 8px;
}
.jd {
  white-space: pre-wrap;
  background: #f5f7fb;
  padding: 14px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.7;
}
@media (max-width: 900px) {
  .grid2 {
    grid-template-columns: 1fr;
  }
}
</style>
