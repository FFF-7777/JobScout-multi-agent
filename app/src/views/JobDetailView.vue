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
    <el-button link type="primary" class="back-btn" @click="router.back()">← 返回</el-button>
    <el-empty v-if="!loading && !job" description="未找到该岗位（可能已被删除）" />
    <div v-else-if="job" class="card-stack">
      <!-- 头部：岗位名片（大卡片） -->
      <div class="card hero-card">
        <div class="hero-title">
          <span class="company">{{ job.company_name || "（待解析）" }}</span>
          <span class="dot">·</span>
          <span class="role">{{ job.job_title || "（待解析）" }}</span>
        </div>
        <div class="hero-tags">
          <el-tag v-if="job.city" size="default" effect="plain">📍 {{ job.city }}</el-tag>
          <el-tag v-if="job.salary" size="default" effect="plain" type="success">
            💰 {{ job.salary }}
          </el-tag>
          <el-tag v-if="job.source" size="default" effect="plain" type="info">
            来源：{{ job.source }}
          </el-tag>
          <el-tag
            v-if="job.job_url"
            size="default"
            effect="plain"
            type="warning"
            style="cursor: pointer"
            @click="window.open(job.job_url, '_blank')"
          >
            🔗 打开原链接
          </el-tag>
        </div>
      </div>

      <!-- 匹配评分卡（仅在有 match 时显示） -->
      <div v-if="match" class="card score-card">
        <div class="card-head">
          <span class="card-icon">🎯</span>
          <span class="card-title">匹配评分</span>
        </div>
        <div class="score-body">
          <div class="score-left">
            <div class="score-grade-row">
              <span :class="['grade-tag', 'grade-' + match.level]">{{ match.level }}</span>
              <div class="score-num">{{ match.score }} <span class="score-unit">分</span></div>
            </div>
            <div class="score-rec">{{ match.recommendation }}</div>
          </div>
          <div v-if="dims" class="dims">
            <div v-for="(v, k) in dims" :key="k" class="dim">
              <el-progress
                type="dashboard"
                :percentage="Math.round(Number(v))"
                :width="68"
                :stroke-width="6"
              />
              <div class="dim-label">{{ DIM_LABELS[k] || k }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 匹配点 / 缺口分析（左右两卡片） -->
      <div v-if="match" class="card-grid">
        <div class="card">
          <div class="card-head">
            <span class="card-icon">✅</span>
            <span class="card-title">匹配点</span>
            <el-tag size="small" type="success" effect="light">
              {{ match.matched_points?.length || 0 }} 项
            </el-tag>
          </div>
          <ul class="point-list success-list">
            <li v-for="p in match.matched_points" :key="p">{{ p }}</li>
          </ul>
        </div>
        <div class="card">
          <div class="card-head">
            <span class="card-icon">⚠️</span>
            <span class="card-title">缺口分析</span>
            <el-tag size="small" type="warning" effect="light">
              {{ match.missing_points?.length || 0 }} 项
            </el-tag>
          </div>
          <ul class="point-list warning-list">
            <li v-for="p in match.missing_points" :key="p">{{ p }}</li>
          </ul>
        </div>
      </div>

      <!-- 风险提示（独立大卡片） -->
      <div
        v-if="match?.risk_notes?.length || report?.risks?.length"
        class="card risk-card"
      >
        <div class="card-head">
          <span class="card-icon">🚩</span>
          <span class="card-title">风险提示</span>
        </div>
        <ul class="point-list danger-list">
          <li v-for="p in [...(match?.risk_notes || []), ...(report?.risks || [])]" :key="p">
            {{ p }}
          </li>
        </ul>
      </div>

      <!-- 投递建议（仅在有 report 时） -->
      <div v-if="report" class="card recommend-card">
        <div class="card-head">
          <span class="card-icon">📌</span>
          <span class="card-title">投递建议</span>
        </div>
        <div class="rec-summary">
          <b class="rec-conclusion">{{ report.conclusion }}</b>
          <span class="rec-divider">·</span>
          <span class="rec-priority">{{ report.priority }}</span>
        </div>
        <ul class="point-list">
          <li v-for="r in report.reasons" :key="r">{{ r }}</li>
        </ul>
      </div>

      <!-- 面试可能问题 / 项目讲解重点（左右两卡片） -->
      <div v-if="report" class="card-grid">
        <div class="card">
          <div class="card-head">
            <span class="card-icon">💬</span>
            <span class="card-title">面试可能问题</span>
          </div>
          <ol class="point-list numbered">
            <li v-for="q in report.interview_questions" :key="q">{{ q }}</li>
          </ol>
        </div>
        <div class="card">
          <div class="card-head">
            <span class="card-icon">🎤</span>
            <span class="card-title">项目讲解重点</span>
          </div>
          <ul class="point-list">
            <li v-for="q in report.project_talking_points" :key="q">{{ q }}</li>
          </ul>
        </div>
      </div>

      <!-- BOSS / HR 话术（左右两卡片） -->
      <div v-if="report" class="card-grid">
        <div class="card">
          <div class="card-head">
            <span class="card-icon">👋</span>
            <span class="card-title">BOSS 打招呼话术</span>
          </div>
          <div class="quote">{{ report.boss_greeting }}</div>
        </div>
        <div class="card">
          <div class="card-head">
            <span class="card-icon">✉️</span>
            <span class="card-title">HR 私信</span>
          </div>
          <div class="quote">{{ report.hr_message }}</div>
        </div>
      </div>

      <!-- 岗位解析 -->
      <div class="card">
        <div class="card-head">
          <span class="card-icon">📋</span>
          <span class="card-title">岗位解析</span>
        </div>
        <div class="chips">
          <span class="chip-title">必备技能</span>
          <el-tag
            v-for="s in job.analysis?.required_skills"
            :key="s"
            type="danger"
            effect="plain"
            style="margin: 3px"
          >
            {{ s }}
          </el-tag>
        </div>
        <div class="chips">
          <span class="chip-title">加分技能</span>
          <el-tag v-for="s in job.analysis?.preferred_skills" :key="s" style="margin: 3px">
            {{ s }}
          </el-tag>
        </div>
        <div class="chips" v-if="job.analysis?.risk_tags?.length">
          <span class="chip-title">风险标签</span>
          <el-tag
            v-for="s in job.analysis?.risk_tags"
            :key="s"
            type="warning"
            style="margin: 3px"
          >
            {{ s }}
          </el-tag>
        </div>
      </div>

      <!-- JD 原文（最后一张大卡片） -->
      <div class="card">
        <div class="card-head">
          <span class="card-icon">📄</span>
          <span class="card-title">JD 原文</span>
        </div>
        <pre class="jd">{{ job.jd_text }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
.back-btn {
  margin-bottom: 8px;
  font-size: 15px;
}

/* === 卡片墙容器 === */
.card-stack {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.card-stack > .card {
  margin-bottom: 0; /* 卡片间距由 gap 控制 */
}

/* === 通用卡片头 === */
.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  font-size: 17px;
  font-weight: 700;
  color: #1f2733;
}
.card-icon {
  font-size: 18px;
}
.card-title {
  flex: 0 0 auto;
}

/* === 头部名片卡 === */
.hero-card {
  padding: 24px 28px;
  background: linear-gradient(135deg, #ffffff 0%, #f7f9ff 100%);
  border: 1px solid #e6ecff;
}
.hero-title {
  font-size: 24px;
  font-weight: 800;
  color: #1f2733;
  margin-bottom: 14px;
  letter-spacing: 0.5px;
}
.hero-title .dot {
  margin: 0 8px;
  color: #b8bfca;
  font-weight: 400;
}
.hero-tags {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

/* === 匹配评分卡 === */
.score-card .score-body {
  display: flex;
  gap: 28px;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
}
.score-grade-row {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 6px;
}
.score-num {
  font-size: 32px;
  font-weight: 800;
  color: #3a6ff7;
  line-height: 1.1;
}
.score-unit {
  font-size: 16px;
  color: #8a94a6;
  font-weight: 500;
}
.score-rec {
  color: #5a6472;
  font-size: 14px;
  line-height: 1.6;
}
.dims {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
}
.dim {
  text-align: center;
}
.dim-label {
  font-size: 12px;
  color: #8a94a6;
  margin-top: 4px;
}

/* === 两栏卡片网格 === */
.card-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
}
.card-grid > .card {
  margin-bottom: 0;
}

/* === 风险卡：左侧红色边线 === */
.risk-card {
  border-left: 4px solid #e64545;
}

/* === 投递建议卡：左侧蓝色边线 === */
.recommend-card {
  border-left: 4px solid #3a6ff7;
}
.rec-summary {
  font-size: 15px;
  margin-bottom: 10px;
  padding: 10px 14px;
  background: #f5f8ff;
  border-radius: 6px;
}
.rec-conclusion {
  color: #1f2733;
  font-weight: 700;
}
.rec-divider {
  margin: 0 8px;
  color: #b8bfca;
}
.rec-priority {
  color: #3a6ff7;
  font-weight: 600;
}

/* === 列表样式 === */
ul,
ol {
  margin: 4px 0;
  padding-left: 22px;
  line-height: 1.85;
  font-size: 15px;
  color: #2c3340;
}
.point-list li {
  margin: 4px 0;
}
.success-list li::marker {
  color: #2fae5f;
}
.warning-list li::marker {
  color: #f7861b;
}
.danger-list li::marker {
  color: #e64545;
}

/* === 话术引用 === */
.quote {
  background: #f7f9ff;
  border-left: 3px solid #3a6ff7;
  padding: 14px 16px;
  border-radius: 6px;
  line-height: 1.75;
  font-size: 15px;
  color: #2c3340;
  white-space: pre-wrap;
}

/* === 岗位解析 chip === */
.chips {
  margin: 8px 0;
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 4px;
}
.chip-title {
  color: #8a94a6;
  font-size: 14px;
  margin-right: 8px;
  min-width: 70px;
  line-height: 28px;
}

/* === JD 原文 === */
.jd {
  white-space: pre-wrap;
  background: #f5f7fb;
  padding: 20px 24px;
  border-radius: 8px;
  font-size: 16px;
  line-height: 1.8;
  color: #2c3340;
  border: 1px solid #ebeef5;
  margin: 0;
}

/* === 响应式 === */
@media (max-width: 900px) {
  .card-grid {
    grid-template-columns: 1fr;
  }
  .score-card .score-body {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
