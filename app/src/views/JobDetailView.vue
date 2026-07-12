<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, type Job, type MatchResult } from "@/api";
import { useAppStore } from "@/stores/app";

const props = withDefaults(
  defineProps<{
    /** 当作为 modal 嵌入时，从父组件传 jobId 覆盖 route.params.id */
    jobIdProp?: number | null;
    /** 当作为 modal 嵌入时，禁用内部跳路由（无效 ID 时不 router.replace） */
    embedded?: boolean;
    /**
     * 显示模式：
     * - 'info'     只显示岗位信息（公司/岗位/JD/解析），用于 JobsView 的弹窗
     * - 'analysis' 只显示分析结果（匹配评分/匹配点/缺口/面试题等），用于 ResultsView 的弹窗
     * - 'all'      都显示（独立 /jobs/:id 路由页）
     */
    mode?: "info" | "analysis" | "all";
  }>(),
  { mode: "all" }
);
const emit = defineEmits<{ (e: "close"): void }>();

const route = useRoute();
const router = useRouter();
const store = useAppStore();

function resolveJobId(): number | null {
  if (typeof props.jobIdProp === "number" && props.jobIdProp > 0) return props.jobIdProp;
  const raw = Number(route.params.id);
  return Number.isFinite(raw) && raw > 0 ? raw : null;
}

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

const showInfo = computed(() => props.mode !== "analysis");
const showAnalysis = computed(() => props.mode !== "info");

async function load(targetId?: number | null) {
  const id = targetId ?? resolveJobId();
  if (id === null) {
    ElMessage.error("无效的岗位 ID");
    if (!props.embedded) {
      router.replace("/jobs");
    } else {
      emit("close");
    }
    return;
  }
  loading.value = true;
  try {
    job.value = await api.getJob(id);
    // 只在需要显示分析结果时才去拉
    if (showAnalysis.value) {
      const results = await api.listResults(store.taskId || undefined);
      match.value = results.find((r) => r.job_id === id) || null;
    } else {
      match.value = null;
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "加载失败");
  } finally {
    loading.value = false;
  }
}

onMounted(() => load());
watch(
  () => props.jobIdProp,
  (newId, oldId) => {
    if (newId !== oldId) load(newId);
  }
);

function goBack() {
  if (props.embedded) {
    emit("close");
  } else {
    router.back();
  }
}

// 跳到推荐结果页（看这个岗位的匹配分析）
function gotoResults() {
  router.push("/results");
}
</script>

<template>
  <div class="page" v-loading="loading">
    <el-button v-if="!embedded" link type="primary" class="back-btn" @click="goBack()">
      ← 返回
    </el-button>
    <el-empty v-if="!loading && !job" description="未找到该岗位（可能已被删除）" />
    <div v-else-if="job" class="big-card">
      <button
        v-if="embedded"
        class="bigcard-close"
        @click="emit('close')"
        title="关闭"
        aria-label="关闭"
      >
        ×
      </button>

      <!-- 头部：岗位名片（两种模式都显示，方便用户识别在看哪个岗位） -->
      <div v-if="showInfo" class="big-card-head">
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

      <!-- ====== 岗位信息区（mode=info / all）====== -->
      <template v-if="showInfo">
        <div v-if="job.analysis" class="sub-card">
          <div class="sub-head">📋 岗位解析</div>
          <div class="chips" v-if="job.analysis.required_skills?.length">
            <span class="chip-title">必备技能</span>
            <el-tag
              v-for="s in job.analysis.required_skills"
              :key="s"
              type="danger"
              effect="plain"
              style="margin: 3px"
            >
              {{ s }}
            </el-tag>
          </div>
          <div class="chips" v-if="job.analysis.preferred_skills?.length">
            <span class="chip-title">加分技能</span>
            <el-tag
              v-for="s in job.analysis.preferred_skills"
              :key="s"
              style="margin: 3px"
            >
              {{ s }}
            </el-tag>
          </div>
          <div class="chips" v-if="job.analysis.risk_tags?.length">
            <span class="chip-title">风险标签</span>
            <el-tag
              v-for="s in job.analysis.risk_tags"
              :key="s"
              type="warning"
              style="margin: 3px"
            >
              {{ s }}
            </el-tag>
          </div>
          <div
            v-if="
              !job.analysis.required_skills?.length &&
              !job.analysis.preferred_skills?.length &&
              !job.analysis.risk_tags?.length
            "
            class="empty-tip"
          >
            （暂无解析结果，可在「岗位导入」页对该岗位点「重新解析」）
          </div>
        </div>

        <div class="sub-card">
          <div class="sub-head">📄 JD 原文</div>
          <pre class="jd">{{ job.jd_text }}</pre>
        </div>
      </template>

      <!-- ====== 分析结果区（mode=analysis / all）====== -->
      <template v-if="showAnalysis">
        <div v-if="!match" class="sub-card">
          <div class="sub-head">🎯 分析结果</div>
          <div class="empty-tip">
            该岗位暂无分析结果。请先到「岗位导入」页选中该岗位 + 简历后跑「开始分析」。
          </div>
        </div>

        <template v-else>
          <div class="section">
            <div class="section-title">🎯 匹配评分</div>
            <div class="score-body">
              <div class="score-left">
                <div class="score-grade-row">
                  <span :class="['grade-tag', 'grade-' + match.level]">{{ match.level }}</span>
                  <div class="score-num">
                    {{ match.score }} <span class="score-unit">分</span>
                  </div>
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

          <div class="grid2">
            <div class="sub-card success">
              <div class="sub-head">✅ 匹配点（{{ match.matched_points?.length || 0 }}）</div>
              <ul>
                <li v-for="p in match.matched_points" :key="p">{{ p }}</li>
              </ul>
            </div>
            <div class="sub-card warning">
              <div class="sub-head">⚠️ 缺口分析（{{ match.missing_points?.length || 0 }}）</div>
              <ul>
                <li v-for="p in match.missing_points" :key="p">{{ p }}</li>
              </ul>
            </div>
          </div>

          <div
            v-if="match?.risk_notes?.length || report?.risks?.length"
            class="sub-card danger"
          >
            <div class="sub-head">🚩 风险提示</div>
            <ul>
              <li
                v-for="p in [...(match?.risk_notes || []), ...(report?.risks || [])]"
                :key="p"
              >
                {{ p }}
              </li>
            </ul>
          </div>

          <div v-if="report" class="sub-card primary">
            <div class="sub-head">📌 投递建议</div>
            <div class="rec-summary">
              <b>{{ report.conclusion }}</b>
              <span class="rec-divider">·</span>
              <span>{{ report.priority }}</span>
            </div>
            <ul>
              <li v-for="r in report.reasons" :key="r">{{ r }}</li>
            </ul>
          </div>

          <div v-if="report" class="grid2">
            <div class="sub-card">
              <div class="sub-head">💬 面试可能问题</div>
              <ol>
                <li v-for="q in report.interview_questions" :key="q">{{ q }}</li>
              </ol>
            </div>
            <div class="sub-card">
              <div class="sub-head">🎤 项目讲解重点</div>
              <ul>
                <li v-for="q in report.project_talking_points" :key="q">{{ q }}</li>
              </ul>
            </div>
          </div>

          <div v-if="report" class="grid2">
            <div class="sub-card">
              <div class="sub-head">👋 BOSS 打招呼话术</div>
              <div class="quote">{{ report.boss_greeting }}</div>
            </div>
            <div class="sub-card">
              <div class="sub-head">✉️ HR 私信</div>
              <div class="quote">{{ report.hr_message }}</div>
            </div>
          </div>
        </template>
      </template>

      <!-- 底部操作：info 模式跳推荐结果；analysis 模式跳 JobsView 弹窗 -->
      <div v-if="embedded" class="big-card-foot">
        <el-button
          v-if="mode === 'info'"
          type="primary"
          plain
          @click="gotoResults"
        >
          查看分析结果（推荐结果页） →
        </el-button>
        <span class="foot-hint">
          {{
            mode === "info"
              ? "匹配评分 / 缺口 / 面试题 / BOSS 话术请到「推荐结果」页查看"
              : mode === "analysis"
                ? "岗位基本信息（公司/JD/解析）请到「岗位导入」页查看"
                : ""
          }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* === 大卡片容器（整张图就是一张卡）=== */
.big-card {
  position: relative;
  background: #ffffff;
  border-radius: 16px;
  padding: 28px 32px 28px;
  box-shadow: 0 4px 24px rgba(20, 40, 90, 0.08);
  border: 1px solid #ebeef5;
}
.big-card-head {
  background: linear-gradient(135deg, #ffffff 0%, #f7f9ff 100%);
  border: 1px solid #e6ecff;
  border-radius: 12px;
  padding: 22px 24px;
  margin-bottom: 20px;
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

/* === 评分块（analysis 模式用）=== */
.section {
  margin: 18px 0;
}
.section-title {
  font-size: 17px;
  font-weight: 700;
  color: #1f2733;
  margin-bottom: 12px;
}
.score-body {
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

/* === 子卡（在大卡内分组）=== */
.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin: 16px 0;
}
.sub-card {
  background: #fafbfd;
  border-radius: 10px;
  padding: 18px 20px;
  border: 1px solid #ebeef5;
  margin-bottom: 16px;
}
.sub-card:last-of-type {
  margin-bottom: 0;
}
.sub-card.success {
  border-left: 4px solid #2fae5f;
}
.sub-card.warning {
  border-left: 4px solid #f7861b;
}
.sub-card.danger {
  border-left: 4px solid #e64545;
  margin: 16px 0;
}
.sub-card.primary {
  border-left: 4px solid #3a6ff7;
  margin: 16px 0;
}
.sub-head {
  font-size: 16px;
  font-weight: 700;
  color: #1f2733;
  margin-bottom: 10px;
}
.empty-tip {
  color: #8a94a6;
  font-size: 13px;
  font-style: italic;
  margin: 0;
}
ul,
ol {
  margin: 4px 0;
  padding-left: 22px;
  line-height: 1.85;
  font-size: 15px;
  color: #2c3340;
}
.rec-summary {
  font-size: 15px;
  margin-bottom: 10px;
  padding: 10px 14px;
  background: #f5f8ff;
  border-radius: 6px;
}
.rec-divider {
  margin: 0 8px;
  color: #b8bfca;
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
.chips:first-of-type {
  margin-top: 0;
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
  font-family: "Microsoft YaHei", "PingFang SC", "Heiti SC", "SimHei", "黑体", sans-serif;
  font-size: 16px;
  line-height: 1.85;
  color: #1f2733;
  font-weight: 500;
  border: 1px solid #ebeef5;
  margin: 0;
}

/* === 底部操作区 === */
.big-card-foot {
  margin-top: 18px;
  padding-top: 18px;
  border-top: 1px dashed #ebeef5;
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.foot-hint {
  color: #8a94a6;
  font-size: 13px;
}

/* === 大卡片右上角 X 按钮（仅 modal 模式显示）=== */
.bigcard-close {
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 10;
  width: 34px;
  height: 34px;
  border: none;
  background: rgba(255, 255, 255, 0.9);
  color: #1f2733;
  font-size: 22px;
  font-weight: 500;
  line-height: 1;
  border-radius: 50%;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
  transition: transform 0.15s, background 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}
.bigcard-close:hover {
  background: #f5f7fb;
  transform: scale(1.1);
}

/* === 响应式 === */
@media (max-width: 900px) {
  .grid2 {
    grid-template-columns: 1fr;
  }
  .score-body {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
