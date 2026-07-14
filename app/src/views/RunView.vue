<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, type AgentRun, type AgentRuntimeMeta, type Job, type WorkflowTask } from "@/api";
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
const taskFinishedAt = ref<number | null>(null);
const nowTick = ref(Date.now());
const taskSessionStartKey = (taskId: string) => `jobscout-task-start:${taskId}`;
const taskSessionFinishKey = (taskId: string) => `jobscout-task-finish:${taskId}`;

function currentSessionTaskStart(taskId: string) {
  const key = taskSessionStartKey(taskId);
  const saved = Number(sessionStorage.getItem(key));
  if (Number.isFinite(saved) && saved > 0) return saved;
  const startedAt = Date.now();
  sessionStorage.setItem(key, String(startedAt));
  return startedAt;
}

function parseApiMillis(value?: string | null) {
  if (!value) return null;
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`;
  const parsed = Date.parse(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

const DEFAULT_RUNTIME_META: AgentRuntimeMeta = {
  job_agent_concurrency: 6,
  match_agent_concurrency: 4,
  report_agent_concurrency: 6,
  match_two_tier: true,
  network_capabilities: {
    quick_analysis: "disabled",
    deep_analysis: "forced_with_model_fallback",
    deep_report: "forced_with_model_fallback",
  },
  assumptions: {
    quick_seconds_per_job: 40,
    deep_seconds_per_job: 120,
    report_overhead_seconds: 5,
  },
};

const STATUS_META: Record<string, { label: string; tone: string }> = {
  pending: { label: "等待中", tone: "muted" },
  running: { label: "执行中", tone: "primary" },
  success: { label: "已完成", tone: "success" },
  failed: { label: "失败", tone: "danger" },
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
  const message = String(step.error_message || "").toLowerCase();
  return (
    step.status === "failed" &&
    (message.includes("用户中止") ||
      message.includes("鐢ㄦ埛涓") ||
      message.includes("aborted") ||
      message.includes("cancel"))
  );
}

function stepStatusKey(step: AgentRun) {
  return isCancelled(step) ? "cancelled" : step.status;
}

function syncTaskTiming(task: WorkflowTask) {
  const startedValues = task.steps
    .map((step) => parseApiMillis(step.started_at))
    .filter((value): value is number => value !== null);
  const startAt = startedValues.length ? Math.min(...startedValues) : currentSessionTaskStart(task.task_id);
  taskStartedAt.value = startAt;
  sessionStorage.setItem(taskSessionStartKey(task.task_id), String(startAt));

  if (!isFinished(task.status)) {
    taskFinishedAt.value = null;
    sessionStorage.removeItem(taskSessionFinishKey(task.task_id));
    return;
  }

  const finishedValues = task.steps
    .map((step) => parseApiMillis(step.finished_at))
    .filter((value): value is number => value !== null);
  const saved = Number(sessionStorage.getItem(taskSessionFinishKey(task.task_id)));
  const finishAt = finishedValues.length
    ? Math.max(...finishedValues)
    : Number.isFinite(saved) && saved > 0
      ? saved
      : Date.now();
  taskFinishedAt.value = Math.max(finishAt, startAt);
  sessionStorage.setItem(taskSessionFinishKey(task.task_id), String(taskFinishedAt.value));
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
const researchedJobCount = computed(
  () => deepJobCount.value
);
const quickRounds = computed(() => {
  const concurrency = Math.max(1, runtime.value.match_agent_concurrency || 1);
  if (!basicJobCount.value) return 0;
  return Math.ceil(basicJobCount.value / concurrency);
});
const deepRounds = computed(() => {
  const concurrency = Math.max(1, runtime.value.match_agent_concurrency || 1);
  const deepTargetCount = runtime.value.match_two_tier ? deepJobCount.value : selectedJobs.value.length;
  if (!deepTargetCount) return 0;
  return Math.ceil(deepTargetCount / concurrency);
});

const failedParsedJobs = computed(
  () => selectedJobs.value.filter((job) => job.parse_status === "failed")
);

const runtime = computed(() => runtimeMeta.value ?? DEFAULT_RUNTIME_META);

const estimatedMatchSeconds = computed(() => {
  if (!selectedJobs.value.length) return 0;
  return (
    quickRounds.value * runtime.value.assumptions.quick_seconds_per_job +
    deepRounds.value * runtime.value.assumptions.deep_seconds_per_job
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
  const endAt = isFinished(status.value) && taskFinishedAt.value ? taskFinishedAt.value : nowTick.value;
  return Math.max(0, Math.round((endAt - taskStartedAt.value) / 1000));
});

const remainingSeconds = computed(() => {
  if (isFinished(status.value) || !running.value || !estimatedTotalSeconds.value) return 0;
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
  () =>
    (matchStep.value?.completed_items ?? 0) > 0 ||
    Number((matchStep.value?.output_json as any)?.count ?? 0) > 0
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

const cancelledStepCount = computed(() =>
  steps.value.filter((step) => isCancelled(step)).length
);

const taskAlert = computed(() => {
  if (running.value && connError.value) {
    return {
      type: "warning" as const,
      title: "与后端连接异常，正在自动重试。",
      description: "任务本身可能仍在后台继续执行，页面会持续轮询恢复状态。",
    };
  }
  if (!status.value) return null;
  if (status.value === "completed") {
    return {
      type: "success" as const,
      title: "分析已完成",
      description: hasMatchResults.value ? "推荐结果已经可查看，后续深度报告可按需生成。" : "任务已结束。",
    };
  }
  if (status.value === "completed_with_errors") {
    return {
      type: "warning" as const,
      title: hasMatchResults.value ? "分析已完成，但存在异常项" : "分析已结束，但未产出可用结果",
      description: hasMatchResults.value
        ? "建议先展开执行明细，检查失败岗位后再决定是否重试。"
        : "建议先展开执行明细，确认失败原因后再重试，本次没有可直接查看的匹配结果。",
    };
  }
  if (status.value === "failed") {
    if (cancelledStepCount.value > 0) {
      return {
        type: "info" as const,
        title: "任务已中断",
        description: "已完成的数据会保留，未完成岗位不会继续执行。",
      };
    }
    return {
      type: "error" as const,
      title: "任务执行失败",
      description: "可以检查执行明细中的错误原因，再重新发起分析。",
    };
  }
  return null;
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
  if (isCancelled(step)) {
    return "该阶段已被用户手动中断，已完成的数据会保留。";
  }
  if (step.agent_name === "Match Agent") {
    if (step.status === "running") {
      if (deepJobCount.value > 0) {
        return step.current_item || `正在并发分析 ${selectedJobs.value.length} 个岗位，其中 ${deepJobCount.value} 个会补充深度研究`;
      }
      return step.current_item || `正在并发分析 ${selectedJobs.value.length} 个岗位`;
    }
    if (step.status === "success") {
      return `已完成 ${step.completed_items} 个岗位匹配` + (researchedJobCount.value ? "；联网结果与失败降级状态请在岗位详情中核验" : "");
    }
    if (step.status === "failed") {
      if (!hasMatchResults.value) {
        return "本阶段执行失败，且没有产出可直接查看的匹配结果。";
      }
      return step.failed_items > 0 ? `本阶段有 ${step.failed_items} 个岗位失败，请展开明细查看原因。` : "本阶段执行失败。";
    }
  }
  if (step.agent_name === "Report Agent") {
    if (hasMatchResults.value) {
      return "基础结果已经可查看，深度报告可在结果页按需生成。";
    }
    if (step.status === "failed") {
      return "由于前序匹配未产出可用结果，本阶段没有继续生成报告。";
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
function researchPhaseLabel(phase?: string) {
  return {
    research_searching: "正在联网检索",
    research_complete: "联网检索完成",
    research_degraded: "联网降级",
    research_skipped: "未触发",
  }[phase || ""] || "匹配分析";
}

async function poll(taskId: string) {
  try {
    const task = await api.getTask(taskId);
    steps.value = task.steps;
    status.value = task.status;
    if (itemRunsOpen.value && !itemRunsLoading.value) void loadItemRuns();
    syncTaskTiming(task);
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
    if (!auto) ElMessage.warning("请先在岗位导入页选择要分析的岗位");
    router.push("/jobs");
    return;
  }

  running.value = true;
  status.value = "running";
  taskStartedAt.value = Date.now();
  taskFinishedAt.value = null;
  notifiedDone = false;
  connError.value = false;

  try {
    const task = await api.runAgents(store.resumeId, activeJobIds.value);
    store.setTask(task.task_id);
    sessionStorage.setItem(taskSessionStartKey(task.task_id), String(taskStartedAt.value));
    sessionStorage.removeItem(taskSessionFinishKey(task.task_id));
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
  if (store.taskId) {
    sessionStorage.removeItem(taskSessionStartKey(store.taskId));
    sessionStorage.removeItem(taskSessionFinishKey(store.taskId));
  }
  store.setTask("");
  steps.value = [];
  status.value = "";
  running.value = false;
  taskStartedAt.value = null;
  taskFinishedAt.value = null;
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
        if (store.taskId) {
    sessionStorage.removeItem(taskSessionStartKey(store.taskId));
    sessionStorage.removeItem(taskSessionFinishKey(store.taskId));
  }
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
        <h1>分析执行台</h1>
        <p>{{ runHeadline }}</p>

        <div class="run-hero-metrics">
          <div class="metric-card">
            <span>当前简历</span>
            <b>{{ store.resumeName || "未选择" }}</b>
          </div>
          <div class="metric-card">
            <span>分析岗位</span>
            <b>{{ selectedJobs.length }} 个</b>
          </div>
          <div class="metric-card">
            <span>深度分析</span>
            <b>{{ deepJobCount }} 个</b>
          </div>
          <div class="metric-card">
            <span>深度研究</span>
            <b>{{ researchedJobCount ? `${researchedJobCount} 个联网` : "本次无深度岗位" }}</b>
          </div>
        </div>
      </div>

      <div class="run-hero-side">
        <div class="status-panel">
          <div class="status-top">
            <span class="status-kicker">运行状态</span>
            <el-tag :type="running ? 'primary' : isFinished(status) ? (status === 'completed' ? 'success' : 'warning') : 'info'">
              {{ taskStatusLabel }}
            </el-tag>
          </div>

          <div class="status-grid">
            <div class="status-box">
              <span>预计总用时</span>
              <b>{{ estimatedTotalSeconds ? fmtEstimate(estimatedTotalSeconds) : "待估算" }}</b>
            </div>
            <div class="status-box">
              <span>剩余时间</span>
              <b>{{ running ? fmtEstimate(remainingSeconds) : isFinished(status) ? "0秒" : "—" }}</b>
            </div>
            <div class="status-box">
              <span>{{ isFinished(status) ? "结束用时" : "已耗时" }}</span>
              <b>{{ running || isFinished(status) ? fmtDuration(elapsedSeconds) : "00:00" }}</b>
            </div>
            <div class="status-box">
              <span>联网能力</span>
              <b>深度分析 / 深度报告已接入</b>
            </div>
          </div>

          <div class="hero-actions">
            <el-button v-if="!running && !status" type="primary" @click="start()">
              开始分析
            </el-button>
            <el-button v-else-if="running" type="primary" loading disabled>
              分析执行中
            </el-button>
            <el-button v-else plain @click="reset">
              重置状态
            </el-button>

            <el-button v-if="running" type="danger" plain :loading="aborting" @click="abort">
              中断任务
            </el-button>

            <el-button type="primary" :disabled="!hasMatchResults" @click="router.push('/results')">
              查看结果
            </el-button>
          </div>
        </div>
      </div>
    </section>

    <el-alert
      v-if="taskAlert"
      :type="taskAlert.type"
      :title="taskAlert.title"
      :description="taskAlert.description"
      :closable="false"
      show-icon
      style="margin-bottom: 14px"
    />

    <section class="card progress-shell">
      <div class="section-head">
        <div>
          <div class="section-title">执行进度</div>
          <div class="section-sub">展示本次会话的实时执行状态与预计剩余时间。</div>
        </div>
        <div class="progress-percent">{{ overallProgress }}%</div>
      </div>
      <div class="countdown-row">
        <span>{{ running ? "正在分析所选岗位，结果会持续写入。" : isFinished(status) ? "本次任务状态已同步。" : "开始后将在这里显示实时进度。" }}</span>
        <b v-if="running">预计剩余 {{ fmtEstimate(remainingSeconds) }}</b>
        <b v-else-if="isFinished(status)">本次分析已结束</b>
        <b v-else>等待开始</b>
      </div>
      <div v-if="deepJobCount > 0" class="research-inline-note">
        深度分析会联网；若联网失败，将由深度模型基于已有材料继续分析。基础分析使用本地材料、不开思考模式。
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
          <span v-for="job in failedParsedJobs.slice(0, 3)" :key="job.id">{{ job.job_title || `岗位 ${job.id}` }}</span>
        </div>
      </article>
    </section>

    <section class="execution-grid">
      <article class="card stage-card" :class="`tone-${stageTone(matchStep)}`">
        <div class="stage-head">
          <div>
            <div class="stage-kicker">Stage 01</div>
            <div class="stage-title">匹配分析</div>
          </div>
          <span class="stage-badge" :class="`tone-${stageTone(matchStep)}`">
            {{ stageLabel(matchStep, "待开始") }}
          </span>
        </div>
        <div class="stage-progress-track">
          <div class="stage-progress-bar" :style="{ width: `${stageProgress(matchStep)}%` }"></div>
        </div>
        <div class="stage-stats">
          <span>总岗位 {{ selectedJobs.length }}</span>
          <span>基础 {{ basicJobCount }} / 深度 {{ deepJobCount }}</span>
          <span v-if="deepJobCount > 0">深度联网已接入</span>
        </div>
        <div class="stage-summary">{{ conciseStepSummary(matchStep, "点击开始后将执行所选岗位的匹配分析。") }}</div>
        <div v-if="matchStep" class="stage-detail-row">
          <span>已完成 <b>{{ matchStep.completed_items }}</b></span>
          <span v-if="matchStep.failed_items > 0" class="danger">失败 <b>{{ matchStep.failed_items }}</b></span>
          <span>处理中 <b>{{ matchStep.in_flight_items?.length ?? 0 }}</b></span>
        </div>
        <div v-if="matchStep?.in_flight_items?.length" class="inflight-tags">
          <span v-for="item in matchStep.in_flight_items" :key="item.job_id">
            {{ item.job_title || `岗位 ${item.job_id}` }}
          </span>
        </div>

        <div class="detail-toggle">
          <el-button link type="primary" size="small" :loading="itemRunsLoading" @click="toggleItemRuns">
            {{ itemRunsOpen ? "收起执行明细" : "查看执行明细" }}
          </el-button>
        </div>

        <div v-if="itemRunsOpen" class="detail-table-wrap">
          <table class="detail-table">
            <thead>
              <tr>
                <th>岗位</th>
                <th>模式</th>
                <th>状态</th>
                <th>当前阶段</th>
                <th>耗时</th>
                <th>错误</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in itemRuns" :key="row.id" :class="{ fail: row.status === 'failed' }">
                <td>{{ row.item_label || `岗位 ${row.item_id}` }}</td>
                <td>{{ row.tier === "deep" ? "深度" : row.tier === "quick" ? "基础" : "—" }}</td>
                <td>{{ row.status === "done" ? "完成" : row.status === "failed" ? "失败" : row.status === "running" ? "执行中" : "排队中" }}</td>
                <td>{{ researchPhaseLabel(row.phase) }}</td>
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
            <div class="stage-title">结果整理</div>
          </div>
          <span class="stage-badge" :class="`tone-${stageTone(reportStep)}`">
            {{ stageLabel(reportStep, "等待匹配完成") }}
          </span>
        </div>
        <div class="stage-progress-track">
          <div class="stage-progress-bar" :style="{ width: `${stageProgress(reportStep)}%` }"></div>
        </div>
        <div class="stage-stats">
          <span>轻量收尾阶段</span>
          <span>匹配结果已实时写入</span>
          <span>不阻塞查看结果</span>
        </div>
        <div class="stage-summary">
          {{ conciseStepSummary(reportStep, "本阶段只同步最终状态、失败明细与报告入口；匹配结果一旦产出，就可以先去结果页查看。") }}
        </div>
        <div class="mini-points">
          <span>推荐结果：完成后可立即查看匹配分与投递建议</span>
          <span>失败岗位：展开执行明细查看原因，回到结果页单项重试</span>
          <span>报告生成：基础 / 深度报告会分别保存到报告导出页</span>
        </div>
      </article>
    </section>

    <section class="compact-agent-row">
      <article class="card compact-agent">
        <div class="compact-title">简历智能体</div>
        <div class="compact-badges">
          <span v-for="item in conciseOutput(steps.find((step) => step.agent_name === 'Resume Agent') || null)" :key="item">
            {{ item }}
          </span>
        </div>
      </article>
      <article class="card compact-agent">
        <div class="compact-title">岗位智能体</div>
        <div class="compact-badges">
          <span v-for="item in conciseOutput(steps.find((step) => step.agent_name === 'Job Agent') || null)" :key="item">
            {{ item }}
          </span>
          <span v-if="!steps.find((step) => step.agent_name === 'Job Agent')">导入阶段已完成结构化解析</span>
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

.countdown-breakdown {
  margin-top: 8px;
  color: #71809a;
  font-size: 13px;
  line-height: 1.7;
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
