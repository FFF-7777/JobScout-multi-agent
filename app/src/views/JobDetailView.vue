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
/** 深度报告有 interview_questions；基础报告只有 interview_focus */
const interviewList = computed(() => {
  const r = report.value as any;
  if (!r) return [];
  return r.interview_questions || r.interview_focus || [];
});

const genDeepBusy = ref(false);
async function genDeep() {
  if (!match.value?.id) return;
  genDeepBusy.value = true;
  try {
    const res = await api.generateReports([match.value.id], "deep");
    if (res.generated > 0) {
      ElMessage.success("已生成深度报告");
      await load();
    } else if (res.errors?.length) {
      ElMessage.error("深度报告生成失败：" + JSON.stringify(res.errors[0]));
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
          <el-tag v-if="job.city" size="default" effect="plain">📍 {{ job.city }}</el-tag>
          <el-tag v-if="job.salary" size="default" effect="plain" type="success">
            💰 {{ job.salary }}
          </el-tag>
          <el-tag v-if="job.analysis?.internship_days_per_week" size="default" effect="plain" type="primary">
            📅 每周 {{ job.analysis.internship_days_per_week }} 天
          </el-tag>
          <el-tag v-if="job.analysis?.internship_duration" size="default" effect="plain" type="primary">
            ⏱️ {{ job.analysis.internship_duration }}
          </el-tag>
          <el-tag v-if="job.analysis?.graduation_years?.length" size="default" effect="plain" type="primary">
            🎓 {{ job.analysis.graduation_years.map((y: number) => y + '届').join(' / ') }}
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

          <!-- ═══ 第二层：核心三模块 ═══ -->
          <div class="core-modules">
            <!-- 核心优势 -->
            <div class="module-card module-success">
              <div class="module-head">✅ 核心优势</div>
              <div v-if="topStrengths.length === 0" class="module-empty">暂无明确优势</div>
              <div v-for="(s, i) in topStrengths" :key="i" class="module-item">
                <div class="mod-title">{{ s.title }}</div>
                <div class="mod-evidence" v-if="s.resume_evidence">📄 {{ s.resume_evidence }}</div>
                <div class="mod-evidence" v-if="s.job_relevance">🎯 {{ s.job_relevance }}</div>
              </div>
            </div>
            <!-- 主要短板 -->
            <div class="module-card module-warning">
              <div class="module-head">⚠️ 主要短板</div>
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
                <div class="mod-evidence" v-if="g.impact">💡 {{ g.impact }}</div>
                <div class="mod-action" v-if="g.action">
                  <span v-if="g.short_term_fixable">✅ 可短期弥补：</span>
                  <span v-else>📆 建议规划：</span>
                  {{ g.action }}
                </div>
              </div>
            </div>
            <!-- 投递前行动 -->
            <div v-if="nextActions.length > 0" class="module-card module-action">
              <div class="module-head">📌 投递前行动</div>
              <div v-for="(a, i) in nextActions" :key="i" class="module-item">
                <span class="action-num">{{ i + 1 }}.</span> {{ a }}
              </div>
            </div>
          </div>

          <!-- ═══ 第三层：可折叠详细证据 ═══ -->
          <div class="detail-block">
            <div class="detail-block-title">📋 详细证据 <span class="hint-muted">（点击展开）</span></div>

            <!-- 硬条件预筛 -->
            <div v-if="hardConditionResult?.items?.length" class="fold-card" :class="{ open: openSections['硬条件'] }">
              <div class="fold-head" @click="toggleSection('硬条件')">
                🚦 硬条件预筛
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
                    <span class="hc-icon">{{ item.status === 'pass' ? '✅' : '❌' }}</span>
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
                📊 五维评分
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
                🔍 完整匹配点与缺口（{{ (match.matched_points?.length || 0) + (match.missing_points?.length || 0) }} 项）
                <el-icon class="fold-icon" :class="{ rotated: openSections['完整匹配点'] }">
                  <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
                </el-icon>
              </div>
              <div v-show="openSections['完整匹配点']" class="fold-body">
                <div class="grid2">
                  <div class="sub-card success" style="margin:0">
                    <div class="sub-head">✅ 匹配点（{{ match.matched_points?.length || 0 }}）</div>
                    <ul><li v-for="p in match.matched_points" :key="p">{{ p }}</li></ul>
                  </div>
                  <div class="sub-card warning" style="margin:0">
                    <div class="sub-head">⚠️ 缺口（{{ match.missing_points?.length || 0 }}）</div>
                    <ul><li v-for="p in match.missing_points" :key="p">{{ p }}</li></ul>
                  </div>
                </div>
              </div>
            </div>

            <!-- 风险提示 -->
            <div v-if="match?.risk_notes?.length || report?.risks?.length" class="fold-card" :class="{ open: openSections['风险提示'] }">
              <div class="fold-head" @click="toggleSection('风险提示')">
                🚩 风险提示
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
                💬 {{ isDeep ? '面试可能问题' : '面试准备重点' }}
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
                      🧠 生成深度报告
                    </el-button>
                  </div>
                  <div class="rec-summary">
                    <b>{{ report.conclusion }}</b>
                    <span class="rec-divider">·</span>
                    <span>{{ report.priority }}</span>
                  </div>
                  <ul v-if="report.reasons?.length">
                    <li v-for="r in report.reasons" :key="r">{{ r }}</li>
                  </ul>
                  <ol v-if="interviewList.length">
                    <li v-for="q in interviewList" :key="q">{{ q }}</li>
                  </ol>
                  <div v-if="report.project_talking_points?.length" style="margin-top:12px">
                    <div class="sub-head" style="font-size:14px">🎤 项目讲解重点</div>
                    <ul><li v-for="q in report.project_talking_points" :key="q">{{ q }}</li></ul>
                  </div>
                  <div v-if="report.boss_greeting || report.hr_message" class="grid2" style="margin-top:12px">
                    <div class="sub-card" style="margin:0"><div class="sub-head">👋 BOSS 打招呼</div>
                      <div class="quote">{{ report.boss_greeting }}</div></div>
                    <div class="sub-card" style="margin:0"><div class="sub-head">✉️ HR 私信</div>
                      <div class="quote">{{ report.hr_message }}</div></div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 原始 JD -->
            <div class="fold-card" :class="{ open: openSections['原始JD'] }">
              <div class="fold-head" @click="toggleSection('原始JD')">
                📄 岗位原始 JD
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

      <!-- 底部：岗位原始 JD 原文（仅 analysis 模式显示在最后，info 模式在上面已经显示过） -->
      <div v-if="showAnalysis" class="sub-card jd-block">
        <div class="sub-head">📄 岗位原始 JD</div>
        <pre class="jd">{{ job.jd_text }}</pre>
      </div>

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
  background: linear-gradient(135deg, #f0f5ff 0%, #e8f0fe 100%);
  border: 1px solid #d6e4ff;
  border-radius: 12px;
  padding: 20px 24px;
  margin: 18px 0;
}
.decision-card.decision-skip { background: linear-gradient(135deg, #fff5f5 0%, #fee 100%); border-color: #fcc; }
.decision-card.decision-selective_apply { background: linear-gradient(135deg, #fffbe6 0%, #fff8d6 100%); border-color: #ffe58f; }
.decision-row { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 12px; }
.decision-left { display: flex; align-items: center; gap: 16px; }
.decision-badge {
  background: #1d7afa; color: #fff; padding: 4px 16px; border-radius: 20px;
  font-weight: 700; font-size: 18px; white-space: nowrap;
}
.decision-skip .decision-badge { background: #f5222d; }
.decision-selective_apply .decision-badge { background: #faad14; color: #1f2733; }
.decision-meta { display: flex; align-items: baseline; gap: 4px; }
.decision-score { font-size: 28px; font-weight: 900; }
.decision-level { color: #5a6472; font-size: 16px; }
.decision-right { color: #5a6472; font-size: 14px; line-height: 1.8; }
.decision-hr strong { color: #1f2733; }
.decision-career strong { color: #1f2733; }
.decision-career .career-detail { color: #8a94a6; }
.decision-confidence { color: #8a94a6; font-size: 12px; }
.decision-summary { margin-top: 12px; padding-top: 12px; border-top: 1px dashed #d6e4ff; color: #3a5f8a; font-size: 15px; line-height: 1.6; }
.hint-icon { cursor: help; margin-left: 4px; color: #8a94a6; }

/* ═══ 核心三模块 ═══ */
.core-modules { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 16px 0; }
.module-card { background: #fafbfd; border-radius: 10px; padding: 16px 18px; border: 1px solid #ebeef5; }
.module-card.module-action { grid-column: 1 / -1; }
.module-head { font-size: 15px; font-weight: 700; color: #1f2733; margin-bottom: 10px; }
.module-empty { color: #8a94a6; font-size: 13px; font-style: italic; }
.module-item { margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid #f0f2f5; }
.module-item:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
.mod-title { font-size: 14px; font-weight: 600; color: #2c3340; margin-bottom: 4px; }
.mod-evidence { font-size: 13px; color: #5a6472; line-height: 1.6; margin: 2px 0; }
.mod-action { font-size: 13px; color: #3a6ff7; line-height: 1.6; margin-top: 4px; }
.action-num { font-weight: 700; color: #3a6ff7; }
.module-success { border-left: 4px solid #2fae5f; }
.module-warning { border-left: 4px solid #f7861b; }
.module-action { border-left: 4px solid #3a6ff7; }

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
  .decision-row { flex-direction: column; }
}
</style>
