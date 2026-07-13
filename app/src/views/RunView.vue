<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, type AgentRun, type AgentRuntimeMeta, type Job } from "@/api";
import { useAppStore } from "@/stores/app";

const router = useRouter();
const route = useRoute();
const store = useAppStore();

const steps = ref<AgentRun[]>([]);
const status = ref("");
const running = ref(false);
const connError = ref(false);
const pageLoading = ref(false);
const runtimeMeta = ref<AgentRuntimeMeta | null>(null);
const allJobs = ref<Job[]>([]);
const itemRuns = ref<any[]>([]);
const itemRunsOpen = ref(false);
const itemRunsLoading = ref(false);
const aborting = ref(false);

let pollTimer: number | null = null;
let tickTimer: number | null = null;
let notifiedDone = false;

const taskStartedAt = ref<number | null>(null);
const nowTick = ref(Date.now());

const DEFAULT_RUNTIME_META: AgentRuntimeMeta = {
  job_agent_concurrency: 6,
  match_agent_concurrency: 4,
  report_agent_concurrency: 6,
  match_two_tier: true,
  assumptions: {
    quick_seconds_per_job: 20,
    deep_seconds_per_job: 90,
    report_overhead_seconds: 5,
  },
};

const STATUS_META: Record<string, { label: string; tone: string }> = {
  pending: { label: "等待中", tone: "muted" },
  running: { label: "执行中", tone: "primary" },
  success: { label: "已完成", tone: "success" },
  failed: { label: "澶辫触", tone: "danger" },
  cancelled: { label: "已中断", tone: "warning" },
};

function stopTimers() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (tickTimer) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
}

function isFinished(taskStatus: string) {
  return ["completed", "completed_with_errors", "failed"].includes(taskStatus);
}

function isCancelled(step: AgentRun) {
  return step.status === "failed" && step.error_message === "鐢ㄦ埛涓";
}

function stepStatusKey(step: AgentRun) {
  return isCancelled(step) ? "cancelled" : step.status;
}

function parseBackendTime(iso?: string | null) {
  if (!iso) return NaN;
  if (/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)) return new Date(iso).getTime();
  return new Date(`${iso}Z`).getTime();
}

function fmtDuration(seconds: number) {
  const safe = Math.max(0, Math.round(seconds));
  const m = Math.floor(safe / 60);
  const s = safe % 60;
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

function fmtEstimate(seconds: number) {
  const safe = Math.max(0, Math.ceil(seconds));
  if (safe >= 3600) {
    const h = Math.floor(safe / 3600);
    const m = Math.ceil((safe % 3600) / 60);
    return `${h}小时${m ? `${m}分钟` : ""}`;
  }
  if (safe >= 60) {
    const m = Math.floor(safe / 60);
    const s = safe % 60;
    return s === 0 ? `${m}分钟` : `${m}分${s}秒`;
  }
  return `${safe}秒`;
}

async function loadRuntimeMeta() {
  try {
    runtimeMeta.value = await api.getAgentRuntimeMeta();
  } catch {
    runtimeMeta.value = DEFAULT_RUNTIME_META;
  }
}

async function loadJobs() {
  try {
    allJobs.value = await api.listJobs();
  } catch {
    allJobs.value = [];
  }
}

const activeJobIds = computed(() => {
  if (store.selectedJobIds.length > 0) return [...store.selectedJobIds];
  return allJobs.value.map((job) => job.id);
});

const selectedJobs = computed(() => {
  const ids = new Set(activeJobIds.value);
  return allJobs.value.filter((job) => ids.has(job.id));
});

const deepJobCount = computed(
  () => selectedJobs.value.filter((job) => (job.analyze_mode || "summary") === "full").length
);
const basicJobCount = computed(() =>
  runtime.value.match_two_tier
    ? selectedJobs.value.filter((job) => (job.analyze_mode || "summary") !== "full").length
    : 0
);

const parsedJobCount = computed(
  () => selectedJobs.value.filter((job) => job.parse_status === "success").length
);

const failedParsedJobs = computed(
  () => selectedJobs.value.filter((job) => job.parse_status === "failed")
);

const runtime = computed(() => runtimeMeta.value ?? DEFAULT_RUNTIME_META);

const estimatedMatchSeconds = computed(() => {
  const concurrency = Math.max(1, runtime.value.match_agent_concurrency || 1);
  const quickRounds = basicJobCount.value ? Math.ceil(basicJobCount.value / concurrency) : 0;
  const deepTargetCount = runtime.value.match_two_tier ? deepJobCount.value : selectedJobs.value.length;
  const deepRounds = deepTargetCount ? Math.ceil(deepTargetCount / concurrency) : 0;
  if (!selectedJobs.value.length) return 0;
  return (
    quickRounds * runtime.value.assumptions.quick_seconds_per_job +
    deepRounds * runtime.value.assumptions.deep_seconds_per_job
  );
});

const estimatedReportSeconds = computed(() => {
  if (!selectedJobs.value.length) return 0;
  return Math.max(0, runtime.value.assumptions.report_overhead_seconds || 0);
});

const estimatedTotalSeconds = computed(
  () => estimatedMatchSeconds.value + estimatedReportSeconds.value
);

const elapsedSeconds = computed(() => {
  if (!taskStartedAt.value) return 0;
  return Math.max(0, Math.round((nowTick.value - taskStartedAt.value) / 1000));
});

const remainingSeconds = computed(() => {
  if (!running.value || !estimatedTotalSeconds.value) return 0;
  return Math.max(0, estimatedTotalSeconds.value - elapsedSeconds.value);
});

const simulatedProgress = computed(() => {
  if (!running.value || !estimatedTotalSeconds.value) return 0;
  const ratio = elapsedSeconds.value / estimatedTotalSeconds.value;
  return Math.max(3, Math.min(95, Math.round(ratio * 100)));
});

const matchStep = computed(() => steps.value.find((step) => step.agent_name === "Match Agent") || null);
const reportStep = computed(() => steps.value.find((step) => step.agent_name === "Report Agent") || null);
const hasMatchResults = computed(
  () => (matchStep.value?.completed_items ?? 0) > 0 || (matchStep.value?.progress ?? 0) > 0
);

const overallProgress = computed(() => {
  if (isFinished(status.value)) return 100;
  if (running.value) return simulatedProgress.value;
  return 0;
});

const taskStatusLabel = computed(() => {
  if (!status.value) return "未开始";
  if (status.value === "completed") return "分析完成";
  if (status.value === "completed_with_errors") return "部分完成";
  if (status.value === "failed") return "执行失败";
  return "执行中";
});

const runHeadline = computed(() => {
  if (!selectedJobs.value.length) return "请先选择岗位";
  if (!status.value) return "前置资源已经就绪，开始后会直接进入匹配分析并持续写入结果。";
  if (status.value === "completed") return "分析已经完成，现在可以直接查看推荐结果。";
  if (status.value === "completed_with_errors") return "分析已结束，但有部分岗位执行异常，请优先检查失败项。";
  if (status.value === "failed") return "本次分析已中断或失败，可以重试。";
  return "正在按岗位选择的基础 / 深度模式执行匹配，结果会持续刷新。";
});

const prerequisiteCards = computed(() => [
  {
    key: "resume",
    title: "简历画像已就绪",
    status: store.resumeId ? "success" : "pending",
    meta: store.resumeName || "未选择简历",
    desc: store.profile ? "候选人画像已生成，可直接用于后续匹配。" : "需要先在简历画像页完成解析。",
  },
  {
    key: "jobs",
    title: "岗位结构化已就绪",
    status:
      parsedJobCount.value === selectedJobs.value.length && selectedJobs.value.length > 0
        ? "success"
        : failedParsedJobs.value.length > 0
          ? "failed"
          : "running",
    meta: `${parsedJobCount.value}/${selectedJobs.value.length || 0} 已完成解析`,
    desc:
      failedParsedJobs.value.length > 0
        ? `有 ${failedParsedJobs.value.length} 个岗位解析失败，建议回到岗位页重试。`
        : "岗位导入阶段已经完成，点击开始后会直接进入匹配分析。",
  },
]);

function stageTone(step: AgentRun | null) {
  if (!step) return "muted";
  return STATUS_META[stepStatusKey(step)]?.tone || "muted";
}

function stageLabel(step: AgentRun | null, idleLabel: string) {
  if (!step) return idleLabel;
  return STATUS_META[stepStatusKey(step)]?.label || idleLabel;
}

function stageProgress(step: AgentRun | null) {
  if (!step) return 0;
  if (step.status === "success" || isCancelled(step) || step.status === "failed") return 100;
  if (step.agent_name === "Match Agent" && running.value) return Math.min(96, simulatedProgress.value);
  return step.progress || 0;
}

function conciseStepSummary(step: AgentRun | null, fallback: string) {
  if (!step) return fallback;
  if (step.agent_name === "Match Agent") {
    if (step.status === "running") {
      return step.current_item || `正在并发分析 ${selectedJobs.value.length} 个岗位`;
    }
    if (step.status === "success") {
      return `已完成 ${step.completed_items} 个岗位匹配`;
    }
  }
  if (step.agent_name === "Report Agent") {
    if (hasMatchResults.value) {
      return "基础结果已经可查看，深度报告可在结果页按需生成。";
    }
  }
  return step.summary || fallback;
}

function conciseOutput(step: AgentRun | null) {
  if (!step?.output_json) return [];
  if (step.agent_name === "Resume Agent") {
    const output = step.output_json as any;
    return [
      `目标方向 ${Array.isArray(output.target_roles) ? output.target_roles.length : 0} 个`,
      `技能 ${Array.isArray(output.skills) ? output.skills.length : 0} 项`,
      output.name ? `候选人 ${output.name}` : "",
    ].filter(Boolean);
  }
  if (step.agent_name === "Job Agent") {
    const output = step.output_json as any;
    return [`已解析岗位 ${output.count ?? selectedJobs.value.length} 个`];
  }
  return [];
}

async function loadItemRuns() {
  if (!store.taskId) return;
  itemRunsLoading.value = true;
  try {
    itemRuns.value = await api.listItemRuns(store.taskId, "Match Agent");
  } catch {
    itemRuns.value = [];
  } finally {
    itemRunsLoading.value = false;
  }
}

function toggleItemRuns() {
  itemRunsOpen.value = !itemRunsOpen.value;
  if (itemRunsOpen.value && itemRuns.value.length === 0) loadItemRuns();
}

function fmtItemDuration(ms: number | null) {
  if (ms == null) return "—";
  const seconds = Math.round(ms / 1000);
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

async function poll(taskId: string) {
  try {
    const task = await api.getTask(taskId);
    steps.value = task.steps;
    status.value = task.status;
    const firstStarted = task.steps.find((step) => step.started_at)?.started_at;
    if (firstStarted) {
      taskStartedAt.value = parseBackendTime(firstStarted);
    } else if (!taskStartedAt.value && task.status === "running") {
      taskStartedAt.value = Date.now();
    }
    if (isFinished(task.status)) {
      running.value = false;
      if (!notifiedDone) {
        notifiedDone = true;
        if (task.status === "completed") {
          ElMessage.success("分析完成，可以查看推荐结果。");
        } else if (task.status === "completed_with_errors") {
          ElMessage.warning("分析已完成，但有部分岗位执行失败。");
        }
      }
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    } else {
      running.value = true;
    }
    connError.value = false;
  } catch {
    connError.value = true;
  }
}

async function start(auto = false) {
  if (!store.resumeId) {
    if (!auto) ElMessage.warning("请先完成简历画像");
    router.push("/resume");
    return;
  }
  if (selectedJobs.value.length === 0) {
    if (!auto) ElMessage.warning("璇峰厛鍦ㄥ矖浣嶅鍏ラ〉閫夋嫨瑕佸垎鏋愮殑宀椾綅");
    router.push("/jobs");
    return;
  }

  running.value = true;
  status.value = "running";
  taskStartedAt.value = Date.now();
  notifiedDone = false;
  connError.value = false;

  try {
    const task = await api.runAgents(store.resumeId, activeJobIds.value);
    store.setTask(task.task_id);
    steps.value = task.steps;
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = window.setInterval(() => poll(task.task_id), 1800);
    await poll(task.task_id);
  } catch (e: any) {
    running.value = false;
    status.value = "failed";
    ElMessage.error(e?.response?.data?.detail || "启动分析失败");
  } finally {
    if (route.query.autostart === "1") {
      router.replace({ path: "/run" });
    }
  }
}

async function abort() {
  if (!store.taskId || !running.value) return;
  try {
    await ElMessageBox.confirm(
      "确定中断当前分析任务吗？已完成结果会保留，未完成岗位会停止继续执行。",
      "中断分析",
      { type: "warning", confirmButtonText: "中断", cancelButtonText: "继续执行" }
    );
  } catch {
    return;
  }
  aborting.value = true;
  try {
    await api.abortTask(store.taskId);
    ElMessage.success("已发送中断指令，页面会继续同步最新状态。");
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "中断失败");
  } finally {
    aborting.value = false;
  }
}

function reset() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  store.setTask("");
  steps.value = [];
  status.value = "";
  running.value = false;
  taskStartedAt.value = null;
  itemRuns.value = [];
  itemRunsOpen.value = false;
  notifiedDone = false;
  ElMessage.success("已重置运行页状态");
}

async function boot() {
  pageLoading.value = true;
  try {
    await Promise.all([loadRuntimeMeta(), loadJobs()]);
    if (store.taskId) {
      await poll(store.taskId);
      if (route.query.autostart === "1" && isFinished(status.value)) {
        store.setTask("");
        steps.value = [];
        status.value = "";
        await start(true);
      } else if (!isFinished(status.value)) {
        pollTimer = window.setInterval(() => poll(store.taskId!), 1800);
      }
    } else if (route.query.autostart === "1") {
      await start(true);
    }
  } finally {
    pageLoading.value = false;
  }
}

onMounted(() => {
  boot();
  tickTimer = window.setInterval(() => {
    nowTick.value = Date.now();
  }, 1000);
});

onUnmounted(() => {
  stopTimers();
});
</script>

<template>
  <div class="page run-page" v-loading="pageLoading">
    <section class="run-hero card">
      <div class="run-hero-main">
        <div class="eyebrow">Execution Flow</div>
        <h1>鍒嗘瀽鎵ц鍙</h1>
        <p>{{ runHeadline }}</p>

        <div class="run-hero-metrics">
          <div class="metric-card">
            <span>褰撳墠绠€鍘</span>
            <b>{{ store.resumeName || "鏈€夋嫨" }}</b>
          </div>
          <div class="metric-card">
            <span>鍒嗘瀽宀椾綅</span>
            <b>{{ selectedJobs.length }} 涓</b>
          </div>
          <div class="metric-card">
            <span>娣卞害鍒嗘瀽</span>
            <b>{{ deepJobCount }} 涓</b>
          </div>
          <div class="metric-card">
            <span>鍖归厤骞跺彂</span>
            <b>{{ runtime.match_agent_concurrency }}</b>
          </div>
        </div>
      </div>

      <div class="run-hero-side">
        <div class="status-panel">
          <div class="status-top">
            <span class="status-kicker">杩愯鐘舵€</span>
            <el-tag :type="running ? 'primary' : isFinished(status) ? (status === 'completed' ? 'success' : 'warning') : 'info'">
              {{ taskStatusLabel }}
            </el-tag>
          </div>

          <div class="status-grid">
            <div class="status-box">
              <span>棰勮鎬荤敤鏃</span>
              <b>{{ estimatedTotalSeconds ? fmtEstimate(estimatedTotalSeconds) : "待估算" }}</b>
            </div>
            <div class="status-box">
              <span>鍓╀綑鏃堕棿</span>
              <b>{{ running ? fmtEstimate(remainingSeconds) : "—" }}</b>
            </div>
            <div class="status-box">
              <span>宸茶€楁椂闂</span>
              <b>{{ running || isFinished(status) ? fmtDuration(elapsedSeconds) : "00:00" }}</b>
            </div>
            <div class="status-box">
              <span>鍚庣骞跺彂</span>
              <b>{{ runtime.match_agent_concurrency }} 璺尮閰</b>
            </div>
          </div>

          <div class="hero-actions">
            <el-button v-if="!running && !status" type="primary" @click="start()">
              寮€濮嬪垎鏋?            </el-button>
            <el-button v-else-if="running" type="primary" loading disabled>
              鍒嗘瀽鎵ц涓?            </el-button>
            <el-button v-else plain @click="reset">
              閲嶇疆鐘舵€?            </el-button>

            <el-button v-if="running" type="danger" plain :loading="aborting" @click="abort">
              涓柇浠诲姟
            </el-button>

            <el-button type="primary" :disabled="!hasMatchResults" @click="router.push('/results')">
              鏌ョ湅缁撴灉
            </el-button>
          </div>
        </div>
      </div>
    </section>

    <el-alert
      v-if="connError && running"
      type="warning"
      :closable="false"
      show-icon
      title="与后端连接异常，正在自动重试。"
      style="margin-bottom: 14px"
    />

    <section class="card progress-shell">
      <div class="section-head">
        <div>
          <div class="section-title">鎵ц杩涘害</div>
          <div class="section-sub">鎸夊綋鍓嶅悗绔苟鍙戞暟涓庢墍閫夊矖浣嶈妯′及绠楋紝鐢ㄤ簬缁欑敤鎴风ǔ瀹氱殑绛夊緟鍙嶉銆</div>
        </div>
        <div class="progress-percent">{{ overallProgress }}%</div>
      </div>
      <div class="countdown-row">
        <span>鍩虹鍒嗘瀽鎸?20 绉?/ 宀椾綅浼扮畻锛屾繁搴﹀垎鏋愭寜 90 绉?/ 宀椾綅浼扮畻锛屽苟鑷姩鎸夊苟鍙戞暟鎶樼畻銆</span>
        <b v-if="running">棰勮鍓╀綑 {{ fmtEstimate(remainingSeconds) }}</b>
        <b v-else-if="isFinished(status)">鏈鍒嗘瀽宸茬粨鏉</b>
        <b v-else>绛夊緟寮€濮</b>
      </div>
      <div class="fake-progress">
        <div class="fake-progress-bar" :style="{ width: `${overallProgress}%` }"></div>
      </div>
    </section>

    <section class="prereq-grid">
      <article
        v-for="card in prerequisiteCards"
        :key="card.key"
        class="card prereq-card"
        :class="`is-${card.status}`"
      >
        <div class="prereq-top">
          <div>
            <div class="prereq-title">{{ card.title }}</div>
            <div class="prereq-meta">{{ card.meta }}</div>
          </div>
          <span class="prereq-badge" :class="`is-${card.status}`">
            {{ card.status === "success" ? "已就绪" : card.status === "failed" ? "需处理" : "准备中" }}
          </span>
        </div>
        <p class="prereq-desc">{{ card.desc }}</p>
        <div v-if="card.key === 'jobs' && failedParsedJobs.length" class="prereq-errors">
          <span v-for="job in failedParsedJobs.slice(0, 3)" :key="job.id">{{ job.job_title || `宀椾綅 ${job.id}` }}</span>
        </div>
      </article>
    </section>

    <section class="execution-grid">
      <article class="card stage-card" :class="`tone-${stageTone(matchStep)}`">
        <div class="stage-head">
          <div>
            <div class="stage-kicker">Stage 01</div>
            <div class="stage-title">鍖归厤鍒嗘瀽</div>
          </div>
          <span class="stage-badge" :class="`tone-${stageTone(matchStep)}`">
            {{ stageLabel(matchStep, "待开始") }}
          </span>
        </div>
        <div class="stage-progress-track">
          <div class="stage-progress-bar" :style="{ width: `${stageProgress(matchStep)}%` }"></div>
        </div>
        <div class="stage-stats">
          <span>鎬诲矖浣?{{ selectedJobs.length }}</span>
          <span>鍩虹 {{ basicJobCount }} / 娣卞害 {{ deepJobCount }}</span>
          <span>骞跺彂 {{ runtime.match_agent_concurrency }}</span>
        </div>
        <div class="stage-summary">{{ conciseStepSummary(matchStep, "点击开始后将并发执行岗位匹配。") }}</div>
        <div v-if="matchStep" class="stage-detail-row">
          <span>宸插畬鎴?<b>{{ matchStep.completed_items }}</b></span>
          <span v-if="matchStep.failed_items > 0" class="danger">澶辫触 <b>{{ matchStep.failed_items }}</b></span>
          <span>澶勭悊涓?<b>{{ matchStep.in_flight_items?.length ?? 0 }}</b></span>
        </div>
        <div v-if="matchStep?.in_flight_items?.length" class="inflight-tags">
          <span v-for="item in matchStep.in_flight_items" :key="item.job_id">
            {{ item.job_title || `宀椾綅 ${item.job_id}` }}
          </span>
        </div>

        <div class="detail-toggle">
          <el-button link type="primary" size="small" :loading="itemRunsLoading" @click="toggleItemRuns">
            {{ itemRunsOpen ? "鏀惰捣鎵ц鏄庣粏" : "鏌ョ湅鎵ц鏄庣粏" }}
          </el-button>
        </div>

        <div v-if="itemRunsOpen" class="detail-table-wrap">
          <table class="detail-table">
            <thead>
              <tr>
                <th>宀椾綅</th>
                <th>妯″紡</th>
                <th>鐘舵€</th>
                <th>鑰楁椂</th>
                <th>閿欒</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in itemRuns" :key="row.id" :class="{ fail: row.status === 'failed' }">
                <td>{{ row.item_label || `宀椾綅 ${row.item_id}` }}</td>
                <td>{{ row.tier === "deep" ? "深度" : row.tier === "quick" ? "基础" : "—" }}</td>
                <td>{{ row.status === "done" ? "完成" : row.status === "failed" ? "失败" : row.status === "running" ? "执行中" : "排队中" }}</td>
                <td>{{ fmtItemDuration(row.duration_ms) }}</td>
                <td class="err">{{ row.error_message || "—" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </article>

      <article class="card stage-card" :class="`tone-${stageTone(reportStep)}`">
        <div class="stage-head">
          <div>
            <div class="stage-kicker">Stage 02</div>
            <div class="stage-title">缁撴灉鏁寸悊</div>
          </div>
          <span class="stage-badge" :class="`tone-${stageTone(reportStep)}`">
            {{ stageLabel(reportStep, "绛夊緟鍖归厤瀹屾垚") }}
          </span>
        </div>
        <div class="stage-progress-track">
          <div class="stage-progress-bar" :style="{ width: `${stageProgress(reportStep)}%` }"></div>
        </div>
        <div class="stage-stats">
          <span>鎶ュ憡鏁寸悊涓鸿交閲忛樁娈</span>
          <span>涓嶄細闃诲鏌ョ湅缁撴灉</span>
        </div>
        <div class="stage-summary">
          {{ conciseStepSummary(reportStep, "匹配结果一旦开始产出，用户就可以直接进入结果页，无需在这里等待完整原始输出。") }}
        </div>
        <div class="mini-points">
          <span>涓嶅啀灞曠ず鍐楅暱鍘熷 JSON</span>
          <span>缁撴灉椤靛彲缁х画鐢熸垚娣卞害鎶ュ憡</span>
          <span>杩愯椤典笓娉ㄧ瓑寰呭弽棣堜笌鐘舵€佹劅鐭</span>
        </div>
      </article>
    </section>

    <section class="compact-agent-row">
      <article class="card compact-agent">
        <div class="compact-title">绠€鍘嗘櫤鑳戒綋</div>
        <div class="compact-badges">
          <span v-for="item in conciseOutput(steps.find((step) => step.agent_name === 'Resume Agent') || null)" :key="item">
            {{ item }}
          </span>
        </div>
      </article>
      <article class="card compact-agent">
        <div class="compact-title">宀椾綅鏅鸿兘浣</div>
        <div class="compact-badges">
          <span v-for="item in conciseOutput(steps.find((step) => step.agent_name === 'Job Agent') || null)" :key="item">
            {{ item }}
          </span>
          <span v-if="!steps.find((step) => step.agent_name === 'Job Agent')">瀵煎叆闃舵宸插畬鎴愮粨鏋勫寲瑙ｆ瀽</span>
        </div>
      </article>
    </section>
  </div>
</template>

<style scoped>
.run-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.run-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.75fr);
  gap: 18px;
  align-items: stretch;
}

.run-hero-main h1 {
  margin: 12px 0 10px;
  color: #122038;
  font-size: 34px;
  line-height: 1.08;
  font-weight: 800;
}

.run-hero-main p {
  margin: 0;
  color: #6b7891;
  font-size: 14px;
  line-height: 1.8;
}

.run-hero-metrics {
  margin-top: 18px;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.metric-card {
  padding: 14px 16px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(171, 186, 217, 0.24);
}

.metric-card span {
  display: block;
  color: #7a8599;
  font-size: 12px;
}

.metric-card b {
  display: block;
  margin-top: 6px;
  color: #18263f;
  font-size: 18px;
}

.status-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 16px;
}

.status-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.status-kicker {
  color: #7a8599;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.status-box {
  padding: 14px 16px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(171, 186, 217, 0.24);
}

.status-box span {
  display: block;
  color: #7a8599;
  font-size: 12px;
}

.status-box b {
  display: block;
  margin-top: 6px;
  color: #15233b;
  font-size: 18px;
}

.hero-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.progress-shell {
  padding-top: 20px;
  padding-bottom: 20px;
}

.section-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
}

.section-title {
  color: #15233b;
  font-size: 20px;
  font-weight: 760;
}

.section-sub {
  margin-top: 4px;
  color: #7a8599;
  font-size: 13px;
  line-height: 1.6;
}

.progress-percent {
  color: #3a6ff7;
  font-size: 30px;
  font-weight: 800;
  line-height: 1;
}

.countdown-row {
  margin-top: 14px;
  display: flex;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
  color: #6b7891;
  font-size: 13px;
}

.countdown-row b {
  color: #15233b;
}

.fake-progress {
  margin-top: 16px;
  height: 14px;
  border-radius: 999px;
  background: rgba(220, 228, 241, 0.85);
  overflow: hidden;
}

.fake-progress-bar {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(135deg, #77b5ff 0%, #5f87ff 45%, #6a63ff 100%);
  transition: width 0.9s ease;
  box-shadow: 0 8px 24px rgba(95, 135, 255, 0.26);
}

.prereq-grid,
.execution-grid,
.compact-agent-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.prereq-card,
.stage-card,
.compact-agent {
  margin-bottom: 0;
}

.prereq-top,
.stage-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}

.prereq-title,
.stage-title,
.compact-title {
  color: #15233b;
  font-size: 18px;
  font-weight: 760;
}

.prereq-meta,
.stage-kicker {
  color: #7a8599;
  font-size: 12px;
}

.stage-kicker {
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 700;
}

.prereq-badge,
.stage-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 30px;
  padding: 0 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.is-success,
.tone-success {
  border-color: rgba(98, 189, 116, 0.3);
}

.prereq-badge.is-success,
.stage-badge.tone-success {
  color: #267543;
  background: rgba(234, 247, 236, 0.92);
}

.prereq-badge.is-failed,
.stage-badge.tone-danger {
  color: #b54545;
  background: rgba(255, 239, 239, 0.92);
}

.prereq-badge.is-running,
.stage-badge.tone-primary {
  color: #285ae6;
  background: rgba(236, 242, 255, 0.92);
}

.prereq-badge.is-pending,
.stage-badge.tone-muted,
.stage-badge.tone-warning {
  color: #74819a;
  background: rgba(244, 246, 250, 0.94);
}

.prereq-desc,
.stage-summary {
  margin: 14px 0 0;
  color: #5f6c85;
  font-size: 14px;
  line-height: 1.75;
}

.prereq-errors,
.compact-badges,
.mini-points,
.inflight-tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 14px;
}

.prereq-errors span,
.compact-badges span,
.mini-points span,
.inflight-tags span {
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(244, 247, 252, 0.96);
  border: 1px solid rgba(171, 186, 217, 0.24);
  color: #5f6c85;
  font-size: 12px;
}

.stage-progress-track {
  margin-top: 14px;
  height: 10px;
  border-radius: 999px;
  background: rgba(224, 231, 242, 0.86);
  overflow: hidden;
}

.stage-progress-bar {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(135deg, #79b8ff 0%, #5f87ff 45%, #6a63ff 100%);
  transition: width 0.9s ease;
}

.tone-success .stage-progress-bar {
  background: linear-gradient(135deg, #66c986 0%, #42b866 100%);
}

.tone-danger .stage-progress-bar {
  background: linear-gradient(135deg, #ff8d8d 0%, #f15f5f 100%);
}

.stage-stats,
.stage-detail-row {
  margin-top: 12px;
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  color: #72809a;
  font-size: 12px;
}

.stage-detail-row b,
.stage-stats b {
  color: #16243c;
}

.stage-detail-row .danger {
  color: #d34a4a;
}

.detail-toggle {
  margin-top: 14px;
}

.detail-table-wrap {
  margin-top: 10px;
  overflow: auto;
}

.detail-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.detail-table th,
.detail-table td {
  padding: 8px 10px;
  border-bottom: 1px solid rgba(227, 233, 242, 0.9);
  text-align: left;
  vertical-align: top;
}

.detail-table th {
  color: #7a8599;
  background: rgba(247, 249, 252, 0.94);
}

.detail-table tr.fail td {
  background: rgba(255, 244, 244, 0.92);
}

.detail-table .err {
  color: #d34a4a;
  word-break: break-word;
}

@media (max-width: 1100px) {
  .run-hero,
  .prereq-grid,
  .execution-grid,
  .compact-agent-row {
    grid-template-columns: 1fr;
  }

  .run-hero-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .run-hero-main h1 {
    font-size: 28px;
  }

  .run-hero-metrics,
  .status-grid {
    grid-template-columns: 1fr;
  }

  .section-head {
    flex-direction: column;
  }
}
</style>
