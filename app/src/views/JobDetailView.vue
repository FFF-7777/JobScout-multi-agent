<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, type Job, type MatchResult } from "@/api";
import { useAppStore } from "@/stores/app";

/** 0-100 渐变：紫(0) → 蓝(50) → 青(75) → 绿(100) */
function scoreColor(p: number): string {
  const v = Math.max(0, Math.min(100, p));
  if (v <= 50) {
    // 紫 → 蓝：#8b5cf6 → #3b82f6
    const t = v / 50;
    return mix("#8b5cf6", "#3b82f6", t);
  } else if (v <= 75) {
    // 蓝 → 青：#3b82f6 → #06b6d4
    const t = (v - 50) / 25;
    return mix("#3b82f6", "#06b6d4", t);
  } else {
    // 青 → 绿：#06b6d4 → #10b981
    const t = (v - 75) / 25;
    return mix("#06b6d4", "#10b981", t);
  }
}
function mix(a: string, b: string, t: number): string {
  const pa = parseInt(a.slice(1), 16);
  const pb = parseInt(b.slice(1), 16);
  const ar = (pa >> 16) & 0xff, ag = (pa >> 8) & 0xff, ab = pa & 0xff;
  const br = (pb >> 16) & 0xff, bg = (pb >> 8) & 0xff, bb = pb & 0xff;
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const bl = Math.round(ab + (bb - ab) * t);
  return `rgb(${r}, ${g}, ${bl})`;
}

const props = withDefaults(
  defineProps<{
    /** 当作为 modal 嵌入时，从父组件传 jobId 覆盖 route.params.id */
    jobIdProp?: number | null;
    /** ResultsView 传入精确匹配结果，避免同岗位的多份简历结果串线 */
    matchResultIdProp?: number | null;
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
const reportMode = computed(() => (match.value?.detail_json?.report as any)?.mode ?? null);
const isDeep = computed(() => reportMode.value === "deep");
const dims = computed(() => match.value?.detail_json?.dimensions ?? null);
const dj = computed(() => match.value?.detail_json ?? {});
/** 新结构化字段（v3） */
const topStrengths = computed(() => (dj.value as any)?.top_strengths ?? []);
const mainGaps = computed(() => (dj.value as any)?.main_gaps ?? []);
const hrScreening = computed(() => (dj.value as any)?.hr_screening ?? null);
const careerAlignment = computed(() => (dj.value as any)?.career_alignment ?? null);
const appDecision = computed(() => (dj.value as any)?.application_decision ?? null);
const nextActions = computed(() => (dj.value as any)?.next_actions ?? []);
const confidence = computed(() => (dj.value as any)?.confidence ?? null);
const hardConditionResult = computed(() => (dj.value as any)?.hard_condition_result ?? null);
const researchSummary = computed(() => (dj.value as any)?.research_summary ?? []);
const researchMetadata = computed(() => (dj.value as any)?.research_metadata ?? null);
const skillEvidenceSummary = computed(() => (dj.value as any)?.skill_evidence_summary ?? null);
const skillEvidence = computed(() => (dj.value as any)?.skill_evidence ?? []);

const DETAIL_SECTIONS = ["硬条件", "五维评分", "完整匹配点", "风险提示", "面试准备", "原始JD"];
const openSections = ref<Record<string, boolean>>({
  "硬条件": false, "五维评分": false, "完整匹配点": false, "风险提示": false,
  "面试准备": false, "原始JD": false,
});
function toggleSection(s: string) {
  openSections.value[s] = !openSections.value[s];
}

/** 投递决策中文标签 */
function decisionLabel(action?: string): string {
  return { priority_apply: "建议投递", apply: "可以投递", selective_apply: "选择性投递", skip: "不建议投递" }[action || ""] || action || "";
}
/** HR 初筛中文标签 */
function hrLabel(result?: string): string {
  return { competitive: "有竞争力", borderline: "存在风险", unlikely: "初筛概率低" }[result || ""] || result || "";
}
function researchStatusLabel(status?: string): string {
  return {
    success: "联网成功",
    degraded: "联网失败，已降级",
    skipped: "未触发",
    disabled: "未开启",
  }[status || "disabled"] || "状态未知";
}
function researchStatusType(status?: string): "success" | "warning" | "info" {
  return status === "success" ? "success" : status === "degraded" ? "warning" : "info";
}
/** 深度报告有 interview_questions；基础报告只有 interview_focus */
const interviewList = computed(() => {
  const r = report.value as any;
  if (!r) return [];
  return r.interview_questions || r.interview_focus || [];
});

const genDeepBusy = ref(false);
const deepTask = ref<{ status: string; total: number; done: number; failed: number; elapsed: number } | null>(null);
const deepProgress = computed(() => {
  if (!deepTask.value?.total) return 0;
  return Math.round(((deepTask.value.done + deepTask.value.failed) / deepTask.value.total) * 100);
});
function fmtWait(seconds: number): string {
  const value = Math.max(0, Math.round(seconds));
  return value >= 60 ? `${Math.floor(value / 60)}分${value % 60}秒` : `${value}秒`;
}
async function genDeep() {
  if (!match.value?.id) return;
  genDeepBusy.value = true;
  try {
    const res = await api.generateReports([match.value.id], "deep");
    if (!("task_id" in res)) return;
    const startedAt = Date.now();
    deepTask.value = { status: res.status, total: res.total_items, done: 0, failed: 0, elapsed: 0 };
    while (Date.now() - startedAt < 15 * 60 * 1000) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      const task = await api.getReportTask(res.task_id);
      deepTask.value = {
        status: task.status,
        total: task.total,
        done: task.done,
        failed: task.failed,
        elapsed: (Date.now() - startedAt) / 1000,
      };
      if (["done", "partial", "failed"].includes(task.status)) {
        if (task.done) {
          ElMessage.success("深度报告已生成，并保存到报告导出页");
          await load();
        } else {
          ElMessage.error("深度报告生成失败");
        }
        break;
      }
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "生成失败");
  } finally {
    genDeepBusy.value = false;
  }
}

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
      // ResultsView 传入结果 ID 时精确查询；独立岗位页再查该岗位最新结果。
      let r: MatchResult | null = null;
      try {
        r = props.matchResultIdProp
          ? await api.getResult(props.matchResultIdProp)
          : await api.getResultByJob(id);
      } catch (e) {
        // 404 / 网络错误退到 listResults
      }
      if (!r && store.taskId) {
        const results = await api.listResults(store.taskId);
        r = results.find((x) => x.job_id === id) || null;
      }
      match.value = r;
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
  () => [props.jobIdProp, props.matchResultIdProp] as const,
  ([newJobId], [oldJobId]) => {
    if (newJobId !== oldJobId || props.matchResultIdProp) load(newJobId);
  }
);

function openUrl(url?: string) {
  if (url) window.open(url, '_blank');
}
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
          <el-tag v-if="job.city" size="default" effect="plain">{{ job.city }}</el-tag>
          <el-tag v-if="job.salary" size="default" effect="plain" type="success">
            {{ job.salary }}
          </el-tag>
          <el-tag v-if="job.analysis?.internship_days_per_week" size="default" effect="plain" type="primary">
            每周 {{ job.analysis.internship_days_per_week }} 天
          </el-tag>
          <el-tag v-if="job.analysis?.internship_duration" size="default" effect="plain" type="primary">
            {{ job.analysis.internship_duration }}
          </el-tag>
          <el-tag v-if="job.analysis?.graduation_years?.length" size="default" effect="plain" type="primary">
            {{ job.analysis.graduation_years.map((y: number) => y + '届').join(' / ') }}
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
            @click="openUrl(job.job_url)"
          >
            打开原链接
          </el-tag>
        </div>
      </div>

      <!-- ====== 岗位信息区（mode=info / all）====== -->
      <template v-if="showInfo">
        <div v-if="job.analysis" class="sub-card">
          <div class="sub-head">智能体结构化解析</div>
          <p v-if="job.analysis.jd_summary" class="analysis-summary">
            {{ job.analysis.jd_summary }}
          </p>

          <div class="overview-grid">
            <div v-if="job.analysis.education" class="overview-item">
              <span class="overview-label">学历要求</span>
              <strong>{{ job.analysis.education }}</strong>
            </div>
            <div v-if="job.analysis.experience" class="overview-item">
              <span class="overview-label">经验要求</span>
              <strong>{{ job.analysis.experience }}</strong>
            </div>
            <div v-if="job.analysis.job_type" class="overview-item">
              <span class="overview-label">岗位类型</span>
              <strong>{{ job.analysis.job_type }}</strong>
            </div>
            <div
              v-if="job.analysis?.internship_days_per_week || job.analysis?.internship_duration"
              class="overview-item"
            >
              <span class="overview-label">实习节奏</span>
              <strong>
                {{
                  [
                    job.analysis?.internship_days_per_week
                      ? `每周 ${job.analysis.internship_days_per_week} 天`
                      : "",
                    job.analysis?.internship_duration || "",
                  ]
                    .filter(Boolean)
                    .join(" · ")
                }}
              </strong>
            </div>
          </div>

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
          <!-- 实习信息（非实习岗位不显示） -->
          <div
            v-if="job.analysis?.internship_days_per_week || job.analysis?.internship_duration || job.analysis?.graduation_years?.length"
            class="internship-info"
          >
            <span class="chip-title">实习要求</span>
            <div class="internship-items">
              <span v-if="job.analysis?.internship_days_per_week" class="internship-item">
                每周 <b>{{ job.analysis.internship_days_per_week }}</b> 天
              </span>
              <span v-if="job.analysis?.internship_duration" class="internship-item">
                周期：<b>{{ job.analysis.internship_duration }}</b>
              </span>
              <span v-if="job.analysis?.graduation_years?.length" class="internship-item">
                毕业年份：<b>{{ job.analysis.graduation_years.join(' / ') }}</b>
              </span>
            </div>
          </div>

          <div
            v-if="job.analysis.responsibilities?.length || job.analysis.requirements?.length"
            class="analysis-section-grid"
          >
            <div v-if="job.analysis.responsibilities?.length" class="structured-panel">
              <div class="structured-head">核心职责</div>
              <ol class="structured-list ordered">
                <li v-for="item in job.analysis.responsibilities" :key="`resp-${item}`">
                  {{ item }}
                </li>
              </ol>
            </div>
            <div v-if="job.analysis.requirements?.length" class="structured-panel">
              <div class="structured-head">任职要求</div>
              <ul class="structured-list">
                <li v-for="item in job.analysis.requirements" :key="`req-${item}`">
                  {{ item }}
                </li>
              </ul>
            </div>
          </div>

          <div
            v-if="
              !job.analysis.required_skills?.length &&
              !job.analysis.preferred_skills?.length &&
              !job.analysis.risk_tags?.length &&
              !job.analysis.responsibilities?.length &&
              !job.analysis.requirements?.length &&
              !job.analysis.jd_summary
            "
            class="empty-tip"
          >
            （暂无解析结果，可在「岗位导入」页对该岗位点「重新解析」）
          </div>
        </div>

        <div class="sub-card">
          <div class="sub-head">JD OCR 识别结果</div>
          <div class="raw-source-note">以下内容为截图 / OCR / 导入链路拿到的原始文本，便于和结构化解析交叉核对。</div>
          <pre class="jd">{{ job.jd_text }}</pre>
        </div>
      </template>

      <!-- ====== 分析结果区（mode=analysis / all）====== -->
      <template v-if="showAnalysis">
        <div v-if="!match" class="sub-card">
          <div class="sub-head">分析结果</div>
          <div class="empty-tip">
            该岗位暂无分析结果。请先到「岗位导入」页选中该岗位 + 简历后跑「开始分析」。
          </div>
        </div>

        <template v-else>
          <!-- ═══ 第一层：投递决策卡 ═══ -->
          <div class="decision-card" :class="'decision-' + (appDecision?.action || 'apply')">
            <div class="decision-row">
              <div class="decision-left">
                <div class="decision-badge">{{ decisionLabel(appDecision?.action) }}</div>
                <div class="decision-meta">
                  <span class="decision-score" :style="{ color: scoreColor(match.score) }">
                    {{ match.score }}<span class="score-unit">分</span>
                  </span>
                  <span class="decision-level">· {{ match.level }} 级</span>
                </div>
              </div>
              <div class="decision-right">
                <div class="decision-hr">
                  HR 初筛：<strong>{{ hrLabel(hrScreening?.likely_result) }}</strong>
                  <el-tooltip v-if="hrScreening?.main_reason" :content="hrScreening.main_reason" placement="top">
                    <span class="hint-icon">ⓘ</span>
                  </el-tooltip>
                </div>
                <div class="decision-career">
                  职业方向：<strong>{{ careerAlignment?.score ?? '—' }} 分</strong>
                  <span v-if="careerAlignment?.analysis" class="career-detail">· {{ careerAlignment.analysis }}</span>
                </div>
                <div class="decision-confidence" v-if="confidence != null">
                  评估置信度：{{ Math.round(confidence) }}%
                </div>
              </div>
            </div>
            <div class="decision-summary">{{ appDecision?.summary || match.recommendation }}</div>
          </div>

          <div v-if="deepTask" class="deep-progress" aria-live="polite">
            <div class="deep-progress-head">
              <div>
                <div class="deep-progress-label">深度报告</div>
                <b>{{ ['done', 'partial'].includes(deepTask.status) ? '分析完成' : '正在生成针对性投递与面试方案' }}</b>
              </div>
              <span>{{ deepProgress }}%</span>
            </div>
            <el-progress :percentage="deepProgress" :stroke-width="7" :show-text="false" />
            <div class="deep-progress-meta">
              <span>{{ deepTask.done }} / {{ deepTask.total }} 已完成</span>
              <span>已等待 {{ fmtWait(deepTask.elapsed) }}</span>
              <span v-if="!['done', 'partial'].includes(deepTask.status)">预计剩余约 {{ fmtWait(Math.max(1, 180 - deepTask.elapsed)) }}</span>
              <span v-else>已保存到报告导出页</span>
            </div>
          </div>

          <!-- ═══ 第二层：核心三模块 ═══ -->
          <div class="core-modules">
            <!-- 核心优势 -->
            <div class="module-card module-success">
              <div class="module-head">核心优势</div>
              <div v-if="topStrengths.length === 0" class="module-empty">暂无明确优势</div>
              <div v-for="(s, i) in topStrengths" :key="i" class="module-item">
                <div class="mod-title">{{ s.title }}</div>
                <div class="mod-evidence" v-if="s.resume_evidence"><span>简历证据</span>{{ s.resume_evidence }}</div>
                <div class="mod-evidence" v-if="s.job_relevance"><span>岗位关联</span>{{ s.job_relevance }}</div>
              </div>
            </div>
            <!-- 主要短板 -->
            <div class="module-card module-warning">
              <div class="module-head">关键缺口</div>
              <div v-if="mainGaps.length === 0" class="module-empty">暂无重大缺口</div>
              <div v-for="(g, i) in mainGaps" :key="i" class="module-item">
                <div class="mod-title">
                  <el-tag size="small"
                    :type="g.severity === 'fatal' ? 'danger' : g.severity === 'major' ? 'warning' : 'info'"
                    effect="dark" style="margin-right: 6px">
                    {{ g.severity === 'fatal' ? '致命' : g.severity === 'major' ? '重要' : '次要' }}
                  </el-tag>
                  {{ g.title }}
                </div>
                <div class="mod-evidence" v-if="g.impact"><span>影响</span>{{ g.impact }}</div>
                <div class="mod-action" v-if="g.action">
                  <span v-if="g.short_term_fixable">投递前可修正</span>
                  <span v-else>需要长期补足</span>
                  {{ g.action }}
                </div>
              </div>
            </div>
            <!-- 投递前行动 -->
            <div v-if="nextActions.length > 0" class="module-card module-action">
              <div class="module-head">投递前行动</div>
              <div v-for="(a, i) in nextActions" :key="i" class="module-item">
                <span class="action-num">{{ i + 1 }}.</span> {{ a }}
              </div>
            </div>
            <div v-if="skillEvidenceSummary" class="module-card module-evidence-card">
              <div class="module-head">技能证据概览</div>
              <div class="evidence-summary-grid">
                <div class="evidence-pill success">
                  <span>直接命中</span>
                  <b>{{ skillEvidenceSummary.confirmed_count ?? 0 }}</b>
                </div>
                <div class="evidence-pill info">
                  <span>部分命中</span>
                  <b>{{ skillEvidenceSummary.partial_count ?? 0 }}</b>
                </div>
                <div class="evidence-pill primary">
                  <span>可迁移</span>
                  <b>{{ skillEvidenceSummary.transferable_count ?? 0 }}</b>
                </div>
                <div class="evidence-pill muted">
                  <span>未体现</span>
                  <b>{{ skillEvidenceSummary.not_shown_count ?? 0 }}</b>
                </div>
              </div>
              <div v-if="skillEvidence.length" class="evidence-list">
                <div
                  v-for="(item, i) in skillEvidence.slice(0, 8)"
                  :key="`evidence-${i}-${item.skill}`"
                  class="evidence-list-item"
                >
                  <div class="evidence-top">
                    <b>{{ item.skill }}</b>
                    <el-tag
                      size="small"
                      :type="
                        item.bucket === 'confirmed'
                          ? 'success'
                          : item.bucket === 'not_shown'
                            ? 'danger'
                            : 'info'
                      "
                    >
                      {{
                        item.bucket === "confirmed"
                          ? "直接命中"
                          : item.bucket === "partial"
                            ? "部分命中"
                            : item.bucket === "transferable"
                              ? "可迁移"
                              : "未体现"
                      }}
                    </el-tag>
                  </div>
                  <div class="evidence-note">
                    <span>岗位要求：</span>{{ item.job_requirement || item.skill }}
                  </div>
                  <div class="evidence-note">
                    <span>简历证据：</span>{{ item.resume_evidence || "当前简历未提供直接证据" }}
                  </div>
                </div>
              </div>
            </div>
            <div v-if="researchMetadata || researchSummary.length" class="module-card module-research">
              <div class="module-head research-head">
                <span>联网研究</span>
                <el-tag :type="researchStatusType(researchMetadata?.status)" size="small" effect="plain">
                  {{ researchStatusLabel(researchMetadata?.status) }}
                </el-tag>
              </div>
              <div class="module-empty" style="margin-bottom: 10px">
                {{ researchMetadata?.reason || "快速分析不会调用联网研究。" }}
              </div>
              <div v-if="researchMetadata?.queries?.length" class="research-meta-row">
                <b>检索词</b>
                <span v-for="query in researchMetadata.queries" :key="query">{{ query }}</span>
              </div>
              <div v-for="(item, i) in researchSummary" :key="`research-${i}`" class="module-item">
                <span class="action-num">{{ i + 1 }}.</span> {{ item }}
              </div>
              <div v-if="researchMetadata?.source_notes?.length" class="research-sources">
                <b>来源说明</b>
                <div v-for="source in researchMetadata.source_notes" :key="source">{{ source }}</div>
              </div>
              <div v-if="researchMetadata?.error" class="research-error">
                失败原因：{{ researchMetadata.error }}
              </div>
            </div>
          </div>

          <!-- ═══ 第三层：可折叠详细证据 ═══ -->
          <div class="detail-block">
            <div class="detail-block-title">详细证据 <span class="hint-muted">按需展开查看</span></div>

            <!-- 硬条件预筛 -->
            <div v-if="hardConditionResult?.items?.length" class="fold-card" :class="{ open: openSections['硬条件'] }">
              <div class="fold-head" @click="toggleSection('硬条件')">
                硬条件预筛
                <span v-if="hardConditionResult.passed" class="hard-pass-tag">全部通过</span>
                <span v-else class="hard-fail-tag">{{ hardConditionResult.hard_failures?.length || 0 }} 项不通过</span>
                <el-icon class="fold-icon" :class="{ rotated: openSections['硬条件'] }">
                  <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
                </el-icon>
              </div>
              <div v-show="openSections['硬条件']" class="fold-body">
                <div class="hard-cond-list">
                  <div v-for="item in hardConditionResult.items" :key="item.name" class="hard-cond-item"
                    :class="item.status === 'pass' ? 'hc-pass' : 'hc-fail'">
                    <span class="hc-icon">{{ item.status === 'pass' ? '通过' : '不通过' }}</span>
                    <span class="hc-name">{{ item.name }}</span>
                    <span class="hc-evidence">简历：{{ item.resume_evidence }}</span>
                    <span class="hc-requirement">岗位：{{ item.job_requirement }}</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- 五维评分 -->
            <div class="fold-card" :class="{ open: openSections['五维评分'] }">
              <div class="fold-head" @click="toggleSection('五维评分')">
                五维评分
                <el-icon class="fold-icon" :class="{ rotated: openSections['五维评分'] }">
                  <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
                </el-icon>
              </div>
              <div v-show="openSections['五维评分']" class="fold-body">
                <div class="score-body">
                  <div class="score-left">
                    <div class="score-grade-row">
                      <span :class="['grade-tag', 'grade-' + match.level]">{{ match.level }}</span>
                      <div class="score-num" :style="{ color: scoreColor(match.score) }">
                        {{ match.score }} <span class="score-unit">分</span>
                      </div>
                    </div>
                    <div class="score-rec">{{ match.recommendation }}</div>
                  </div>
                  <div v-if="dims" class="dims">
                    <div v-for="(v, k) in dims" :key="k" class="dim">
                      <el-progress type="dashboard" :percentage="Math.round(Number(v))"
                        :width="80" :stroke-width="8" :color="scoreColor(Number(v))" />
                      <div class="dim-label">{{ DIM_LABELS[k] || k }}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 完整匹配点/缺口 -->
            <div class="fold-card" :class="{ open: openSections['完整匹配点'] }">
              <div class="fold-head" @click="toggleSection('完整匹配点')">
                完整匹配点与缺口（{{ (match.matched_points?.length || 0) + (match.missing_points?.length || 0) }} 项）
                <el-icon class="fold-icon" :class="{ rotated: openSections['完整匹配点'] }">
                  <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
                </el-icon>
              </div>
              <div v-show="openSections['完整匹配点']" class="fold-body">
                <div class="grid2">
                  <div class="sub-card success" style="margin:0">
                    <div class="sub-head">匹配点（{{ match.matched_points?.length || 0 }}）</div>
                    <ul><li v-for="p in match.matched_points" :key="p">{{ p }}</li></ul>
                  </div>
                  <div class="sub-card warning" style="margin:0">
                    <div class="sub-head">缺口（{{ match.missing_points?.length || 0 }}）</div>
                    <ul><li v-for="p in match.missing_points" :key="p">{{ p }}</li></ul>
                  </div>
                </div>
              </div>
            </div>

            <!-- 风险提示 -->
            <div v-if="match?.risk_notes?.length || report?.risks?.length" class="fold-card" :class="{ open: openSections['风险提示'] }">
              <div class="fold-head" @click="toggleSection('风险提示')">
                风险提示
                <el-icon class="fold-icon" :class="{ rotated: openSections['风险提示'] }">
                  <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
                </el-icon>
              </div>
              <div v-show="openSections['风险提示']" class="fold-body">
                <div class="sub-card danger" style="margin:0">
                  <ul>
                    <li v-for="p in [...(match?.risk_notes || []), ...(report?.risks || [])]" :key="p">{{ p }}</li>
                  </ul>
                </div>
              </div>
            </div>

            <!-- 面试准备 -->
            <div v-if="report" class="fold-card" :class="{ open: openSections['面试准备'] }">
              <div class="fold-head" @click="toggleSection('面试准备')">
                {{ isDeep ? '深度面试策略' : '面试准备重点' }}
                <el-icon class="fold-icon" :class="{ rotated: openSections['面试准备'] }">
                  <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
                </el-icon>
              </div>
              <div v-show="openSections['面试准备']" class="fold-body">
                <div class="sub-card" style="margin:0">
                  <div class="sub-head">
                    <el-tag size="small" :type="isDeep ? 'success' : 'info'" style="margin-left:0">
                      {{ isDeep ? '深度报告' : '基础报告' }}
                    </el-tag>
                    <el-button v-if="!isDeep" size="small" type="primary" plain
                      style="margin-left:auto" :loading="genDeepBusy" @click="genDeep">
                      生成深度报告
                    </el-button>
                  </div>
                  <div class="rec-summary">
                    <b>{{ report.conclusion }}</b>
                    <span class="rec-divider">·</span>
                    <span>{{ report.priority }}</span>
                  </div>
                  <p v-if="report.executive_summary" class="executive-summary">{{ report.executive_summary }}</p>
                  <div v-if="report.decision_basis?.length" class="report-section">
                    <div class="sub-head" style="font-size:14px">决策依据</div>
                    <ol><li v-for="item in report.decision_basis" :key="item">{{ item }}</li></ol>
                  </div>
                  <ul v-if="report.reasons?.length">
                    <li v-for="r in report.reasons" :key="r">{{ r }}</li>
                  </ul>
                  <div v-if="report.interview_guides?.length" class="interview-guides">
                    <article v-for="(guide, index) in report.interview_guides" :key="index" class="interview-guide">
                      <div class="guide-number">{{ index + 1 }}</div>
                      <div>
                        <h4>{{ guide.question }}</h4>
                        <dl>
                          <div><dt>考察意图</dt><dd>{{ guide.why_asked }}</dd></div>
                          <div><dt>回答框架</dt><dd>{{ guide.answer_framework }}</dd></div>
                          <div><dt>可用证据</dt><dd>{{ guide.evidence }}</dd></div>
                        </dl>
                      </div>
                    </article>
                  </div>
                  <ol v-else-if="interviewList.length">
                    <li v-for="q in interviewList" :key="q">{{ q }}</li>
                  </ol>
                  <div v-if="report.resume_rewrites?.length" class="report-section">
                    <div class="sub-head" style="font-size:14px">简历定向改写</div>
                    <ul><li v-for="item in report.resume_rewrites" :key="item">{{ item }}</li></ul>
                  </div>
                  <div v-if="report.project_talking_points?.length" style="margin-top:12px">
                    <div class="sub-head" style="font-size:14px">项目讲解重点</div>
                    <ul><li v-for="q in report.project_talking_points" :key="q">{{ q }}</li></ul>
                  </div>
                  <div v-if="report.boss_greeting || report.hr_message" class="grid2" style="margin-top:12px">
                    <div class="sub-card" style="margin:0"><div class="sub-head">BOSS 打招呼</div>
                      <div class="quote">{{ report.boss_greeting }}</div></div>
                    <div class="sub-card" style="margin:0"><div class="sub-head">HR 私信</div>
                      <div class="quote">{{ report.hr_message }}</div></div>
                  </div>
                  <div v-if="report.questions_to_ask?.length" class="report-section">
                    <div class="sub-head" style="font-size:14px">建议反问面试官</div>
                    <ul><li v-for="item in report.questions_to_ask" :key="item">{{ item }}</li></ul>
                  </div>
                  <div v-if="report.action_plan?.length" class="report-section">
                    <div class="sub-head" style="font-size:14px">行动清单</div>
                    <ol><li v-for="item in report.action_plan" :key="item">{{ item }}</li></ol>
                  </div>
                </div>
              </div>
            </div>

            <!-- 原始 JD -->
            <div class="fold-card" :class="{ open: openSections['原始JD'] }">
              <div class="fold-head" @click="toggleSection('原始JD')">
                岗位原始 JD
                <el-icon class="fold-icon" :class="{ rotated: openSections['原始JD'] }">
                  <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
                </el-icon>
              </div>
              <div v-show="openSections['原始JD']" class="fold-body">
                <pre class="jd">{{ job.jd_text }}</pre>
              </div>
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
  font-size: 44px;             /* 32 → 44，跟 5 个仪表盘视觉一致 */
  font-weight: 900;
  color: #3a6ff7;
  line-height: 1.1;
  letter-spacing: -1px;
  transition: color 0.3s ease;
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
  display: flex;
  align-items: center;
}
.analysis-summary {
  margin: 0 0 16px;
  padding: 14px 16px;
  border-radius: 10px;
  background: linear-gradient(135deg, #f6f9ff 0%, #fbfcff 100%);
  border: 1px solid #e6ecff;
  color: #344054;
  font-size: 14px;
  line-height: 1.8;
}

.overview-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.overview-item {
  padding: 12px 14px;
  border-radius: 10px;
  background: #ffffff;
  border: 1px solid #ebeff5;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
}

.overview-label {
  display: block;
  margin-bottom: 6px;
  color: #8a94a6;
  font-size: 12px;
  font-weight: 600;
}

.overview-item strong {
  color: #1f2733;
  font-size: 14px;
  line-height: 1.6;
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

/* === 实习信息 === */
.internship-info {
  margin: 8px 0;
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 4px;
}
.internship-items {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.internship-item {
  font-size: 14px;
  color: #5a6472;
  background: #f0f5ff;
  padding: 3px 12px;
  border-radius: 6px;
  line-height: 28px;
}
.internship-item b {
  color: #3a6ff7;
}

.analysis-section-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 14px;
}

.structured-panel {
  padding: 16px 16px 14px;
  border-radius: 12px;
  background: #ffffff;
  border: 1px solid #e9edf5;
}

.structured-head {
  margin-bottom: 10px;
  color: #1f2733;
  font-size: 15px;
  font-weight: 700;
}

.structured-list {
  margin: 0;
  padding-left: 20px;
  color: #344054;
  font-size: 14px;
  line-height: 1.8;
}

.structured-list.ordered {
  padding-left: 22px;
}

/* === JD 原文 === */
.raw-source-note {
  margin-bottom: 12px;
  color: #8a94a6;
  font-size: 13px;
  line-height: 1.6;
}

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
  max-height: 360px;            /* 大卡底部 JD 给个上限，避免撑爆 */
  overflow-y: auto;
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

/* ═══ 决策卡 ═══ */
.decision-card {
  background: #ffffff;
  border: 1px solid #dfe5ee;
  border-top: 3px solid #356ae6;
  border-radius: 12px;
  padding: 22px 24px 20px;
  margin: 18px 0;
  box-shadow: 0 5px 18px rgba(27, 39, 59, 0.05);
}
.decision-card.decision-skip { background: #fff; border-color: #eadede; border-top-color: #c94747; }
.decision-card.decision-selective_apply { background: #fff; border-color: #e8e1d2; border-top-color: #c78a2c; }
.decision-row { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 12px; }
.decision-left { display: flex; align-items: center; gap: 16px; }
.decision-badge {
  background: #eaf0ff; color: #2857c5; padding: 5px 12px; border-radius: 7px;
  font-weight: 700; font-size: 15px; white-space: nowrap;
}
.decision-skip .decision-badge { background: #fff0f0; color: #b93838; }
.decision-selective_apply .decision-badge { background: #fff6e6; color: #9a6417; }
.decision-meta { display: flex; align-items: baseline; gap: 4px; }
.decision-score { font-size: 28px; font-weight: 900; }
.decision-level { color: #5a6472; font-size: 16px; }
.decision-right { color: #5a6472; font-size: 14px; line-height: 1.8; }
.decision-hr strong { color: #1f2733; }
.decision-career strong { color: #1f2733; }
.decision-career .career-detail { color: #8a94a6; }
.decision-confidence { color: #8a94a6; font-size: 12px; }
.decision-summary { margin-top: 14px; padding-top: 14px; border-top: 1px solid #e7ebf1; color: #344054; font-size: 15px; line-height: 1.65; }
.hint-icon { cursor: help; margin-left: 4px; color: #8a94a6; }

/* ═══ 核心三模块 ═══ */
.core-modules { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 16px 0; }
.module-card { background: #ffffff; border-radius: 10px; padding: 18px 20px; border: 1px solid #e1e6ed; }
.module-card.module-action { grid-column: 1 / -1; }
.module-card.module-evidence-card { grid-column: 1 / -1; }
.module-card.module-research { grid-column: 1 / -1; }
.module-head { font-size: 15px; font-weight: 700; color: #1f2733; margin-bottom: 10px; }
.module-empty { color: #8a94a6; font-size: 13px; font-style: italic; }
.module-item { margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid #f0f2f5; }
.module-item:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
.mod-title { font-size: 14px; font-weight: 600; color: #2c3340; margin-bottom: 4px; }
.mod-evidence { display: grid; grid-template-columns: 64px 1fr; gap: 7px; font-size: 13px; color: #5a6472; line-height: 1.65; margin: 5px 0; }
.mod-evidence span { color: #8a94a6; font-size: 12px; font-weight: 650; }
.mod-action { font-size: 13px; color: #344054; line-height: 1.65; margin-top: 7px; padding: 9px 10px; background: #f6f8fb; border-radius: 6px; }
.mod-action span { display: block; margin-bottom: 2px; color: #667085; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }
.action-num { font-weight: 700; color: #3a6ff7; }
.module-success { border-top: 2px solid #55a476; }
.module-warning { border-top: 2px solid #c58a3b; }
.module-action { border-top: 2px solid #5279d8; }
.module-evidence-card { border-top: 2px solid #6b7bf0; }
.module-research { border-top: 2px solid #4b83ff; background: linear-gradient(180deg, #fff 0%, #f8fbff 100%); }
.research-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.research-meta-row { display: flex; flex-wrap: wrap; gap: 7px; margin: 10px 0 14px; color: #60708c; font-size: 12px; }
.research-meta-row b { width: 100%; color: #344054; }
.research-meta-row span { padding: 5px 9px; border: 1px solid #dbe5ff; border-radius: 999px; background: #f4f7ff; }
.research-sources { margin-top: 12px; color: #60708c; font-size: 12px; line-height: 1.7; }
.research-sources b { display: block; margin-bottom: 4px; color: #344054; }
.research-error { margin-top: 10px; color: #b54708; font-size: 12px; overflow-wrap: anywhere; }

.evidence-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.evidence-pill {
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid #e8edf5;
  background: #fafbfd;
}
.evidence-pill span {
  display: block;
  margin-bottom: 6px;
  color: #7b8798;
  font-size: 12px;
  font-weight: 650;
}
.evidence-pill b {
  color: #1f2733;
  font-size: 20px;
  font-weight: 800;
}
.evidence-pill.success { background: #f3fbf6; border-color: #d7efdf; }
.evidence-pill.info { background: #f6f8fc; border-color: #e3e8f1; }
.evidence-pill.primary { background: #f3f7ff; border-color: #dce6ff; }
.evidence-pill.muted { background: #fbfbfc; border-color: #eceff3; }

.evidence-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.evidence-list-item {
  padding: 14px;
  border-radius: 10px;
  border: 1px solid #e7ecf3;
  background: #fff;
}
.evidence-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.evidence-note {
  color: #556274;
  font-size: 13px;
  line-height: 1.7;
}
.evidence-note span {
  color: #8a94a6;
}

.deep-progress {
  margin: 16px 0;
  padding: 16px 18px;
  border: 1px solid #dce4f2;
  border-radius: 10px;
  background: #f8faff;
}
.deep-progress-head { display: flex; align-items: flex-end; justify-content: space-between; margin-bottom: 10px; }
.deep-progress-label { margin-bottom: 2px; color: #697386; font-size: 11px; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; }
.deep-progress-head > span { color: #2857c5; font-size: 20px; font-weight: 750; }
.deep-progress-meta { display: flex; flex-wrap: wrap; gap: 8px 20px; margin-top: 10px; color: #697386; font-size: 12px; }

.executive-summary { margin: 12px 0 18px; color: #344054; line-height: 1.75; }
.report-section { margin-top: 20px; padding-top: 16px; border-top: 1px solid #e8ebf0; }
.interview-guides { display: grid; gap: 10px; margin-top: 14px; }
.interview-guide { display: grid; grid-template-columns: 28px 1fr; gap: 10px; padding: 14px; border: 1px solid #e2e7ef; border-radius: 8px; background: #fff; }
.guide-number { display: grid; place-items: center; width: 24px; height: 24px; border-radius: 50%; background: #eaf0ff; color: #2857c5; font-size: 12px; font-weight: 700; }
.interview-guide h4 { margin: 1px 0 10px; color: #202b3d; font-size: 14px; }
.interview-guide dl { margin: 0; }
.interview-guide dl > div { display: grid; grid-template-columns: 66px 1fr; gap: 8px; margin: 6px 0; font-size: 13px; line-height: 1.6; }
.interview-guide dt { color: #7a8493; font-weight: 650; }
.interview-guide dd { margin: 0; color: #465266; }

/* ═══ 折叠详情 ═══ */
.detail-block { margin: 16px 0; }
.detail-block-title { font-size: 16px; font-weight: 700; color: #1f2733; margin-bottom: 12px; }
.hint-muted { color: #8a94a6; font-weight: 400; font-size: 13px; }
.fold-card { border: 1px solid #ebeef5; border-radius: 10px; margin-bottom: 10px; overflow: hidden; }
.fold-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 16px; cursor: pointer; font-size: 14px; font-weight: 600; color: #1f2733;
  background: #fafbfd; user-select: none; transition: background 0.15s;
}
.fold-head:hover { background: #f0f2f5; }
.fold-icon { color: #8a94a6; transition: transform 0.2s; }
.fold-icon.rotated { transform: rotate(-180deg); }
.fold-body { padding: 14px 16px; border-top: 1px solid #ebeef5; }

/* === 硬条件预筛 === */
.hard-pass-tag { font-size: 12px; color: #2fae5f; margin-left: auto; margin-right: 8px; }
.hard-fail-tag { font-size: 12px; color: #e64545; margin-left: auto; margin-right: 8px; }
.hard-cond-list { display: flex; flex-direction: column; gap: 10px; }
.hard-cond-item {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 8px 12px; border-radius: 6px; font-size: 14px;
}
.hc-pass { background: #f0faf2; border: 1px solid #d4edda; }
.hc-fail { background: #fff5f5; border: 1px solid #fcc; }
.hc-icon { font-size: 16px; }
.hc-name { font-weight: 700; color: #1f2733; }
.hc-evidence { color: #5a6472; font-size: 13px; }
.hc-requirement { color: #5a6472; font-size: 13px; }

@media (max-width: 900px) {
  .grid2 { grid-template-columns: 1fr; }
  .score-body { flex-direction: column; align-items: flex-start; }
  .core-modules { grid-template-columns: 1fr; }
  .evidence-summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .evidence-list { grid-template-columns: 1fr; }
  .decision-row { flex-direction: column; }
  .overview-grid { grid-template-columns: 1fr; }
  .analysis-section-grid { grid-template-columns: 1fr; }
}
</style>
