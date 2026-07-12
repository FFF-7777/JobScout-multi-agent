<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, type AgentRun } from "@/api";
import { useAppStore } from "@/stores/app";

const router = useRouter();
const store = useAppStore();

const steps = ref<AgentRun[]>([]);
const status = ref<string>("");
const running = ref(false);
const connError = ref(false);
let pollTimer: number | null = null;
let tickTimer: number | null = null;

// 任务起始时间（毫秒）。轮询时若后端未给 started_at，用本地时间兜底。
const taskStartedAt = ref<number | null>(null);
// 实时秒表：每秒自增
const nowTick = ref<number>(Date.now());

const STATUS_META: Record<string, { icon: string; color: string; label: string; type: any }> = {
  pending: { icon: "○", color: "#b8bfca", label: "等待中", type: "info" },
  running: { icon: "◔", color: "#3a6ff7", label: "执行中", type: "primary" },
  success: { icon: "✓", color: "#2fae5f", label: "完成", type: "success" },
  failed: { icon: "✕", color: "#e64545", label: "失败", type: "danger" },
};

const TASK_STATUS_META: Record<string, { label: string; type: any }> = {
  running: { label: "执行中", type: "primary" },
  completed: { label: "全部完成", type: "success" },
  completed_with_errors: { label: "部分完成", type: "warning" },
  failed: { label: "执行失败", type: "danger" },
};

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (tickTimer) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
}

function isFinished(s: string) {
  return ["completed", "failed", "completed_with_errors"].includes(s);
}

function fmtDuration(seconds: number) {
  seconds = Math.max(0, Math.round(seconds));
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

/** 后端 SQLAlchemy DateTime 序列化成 naive ISO（无时区），
 *  实际存的是 UTC（datetime.utcnow() / func.now()）。前端 new Date() 会按本地时间解析，
 *  导致 UTC vs GMT+8 偏差 8 小时。这里统一补 Z 当 UTC 解析。 */
function parseBackendTime(iso: string | null | undefined): number {
  if (!iso) return NaN;
  // 已经有 Z 或 +HH:MM 时区信息，直接走
  if (/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)) {
    return new Date(iso).getTime();
  }
  // naive ISO（YYYY-MM-DDTHH:MM:SS[.fff]）→ 当 UTC
  return new Date(iso + "Z").getTime();
}

function stepElapsedMs(step: AgentRun): number {
  // 优先用后端 started_at/finished_at，本地秒表兜底
  if (step.started_at) {
    const start = parseBackendTime(step.started_at);
    const end = step.finished_at ? parseBackendTime(step.finished_at) : nowTick.value;
    return Math.max(0, end - start);
  }
  if (taskStartedAt.value) {
    return Math.max(0, nowTick.value - taskStartedAt.value);
  }
  return 0;
}

async function poll(taskId: string) {
  try {
    const t = await api.getTask(taskId);
    steps.value = t.steps;
    status.value = t.status;
    if (isFinished(t.status)) {
      // 任务结束：清掉任务起始时间，已耗时归零；停止秒表避免空转
      taskStartedAt.value = null;
      running.value = false;
      stopPoll();
    } else {
      // 用第一个 step 的 started_at 当作任务起始时间（仅在 running 时）
      const firstStarted = t.steps.find((s) => s.started_at)?.started_at;
      if (firstStarted && taskStartedAt.value === null) {
        taskStartedAt.value = parseBackendTime(firstStarted);
      } else if (!firstStarted && taskStartedAt.value === null) {
        taskStartedAt.value = Date.now();
      }
    }
    connError.value = false;
  } catch {
    connError.value = true;
    /* 单次轮询错误，等待下次重试 */
  }
}

async function start() {
  if (!store.resumeId) {
    ElMessage.warning("请先解析简历");
    router.push("/resume");
    return;
  }
  running.value = true;
  status.value = "running";
  taskStartedAt.value = Date.now();
  try {
    // 优先用 JobsView 选中的；没选则跑全部并提示
    let jobIds: number[];
    if (store.selectedJobIds.length > 0) {
      jobIds = [...store.selectedJobIds];
    } else {
      const jobs = await api.listJobs();
      if (!jobs.length) {
        running.value = false;
        ElMessage.warning("请先导入岗位再开始分析");
        router.push("/jobs");
        return;
      }
      jobIds = jobs.map((j) => j.id);
      ElMessage.info(`未在 JobsView 选择，已分析全部 ${jobIds.length} 个岗位`);
    }
    const t = await api.runAgents(store.resumeId, jobIds);
    store.setTask(t.task_id);
    steps.value = t.steps;
    pollTimer = window.setInterval(() => poll(t.task_id), 1800);
    if (!tickTimer) {
      tickTimer = window.setInterval(() => (nowTick.value = Date.now()), 1000);
    }
  } catch (e: any) {
    running.value = false;
    ElMessage.error(e?.response?.data?.detail || "启动失败");
  }
}

const aborting = ref(false);
async function abort() {
  const tid = store.taskId;
  if (!tid || !running.value) return;
  try {
    await ElMessageBox.confirm(
      "确定中断当前任务？已完成的步骤会保留，未完成的会标记为失败。",
      "中断任务",
      { type: "warning", confirmButtonText: "中断", cancelButtonText: "继续" }
    );
  } catch {
    return;
  }
  aborting.value = true;
  try {
    await api.abortTask(tid);
    ElMessage.success("已发送中断指令，后台会尽快停止");
    // 不立刻 stopPoll：让下一次 poll 自然拉到 failed 状态后清理
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "中断失败");
  } finally {
    aborting.value = false;
  }
}

function fmt(v: any) {
  return JSON.stringify(v, null, 2);
}

function progressPercent(step: AgentRun): number {
  if (step.status === "pending") return 0;
  if (["success", "failed"].includes(step.status)) return 100;
  return step.progress || 0;
}

// 把 4 个节点拼成一条总进度：step 1-4 各占 25%，节点内 0-100 映射
const overallProgress = computed(() => {
  if (!steps.value.length) return 0;
  const completedNodes = steps.value.filter(
    (s) => s.status === "success" || s.status === "failed"
  ).length;
  const runningNode = steps.value.find((s) => s.status === "running");
  const baseFromDone = completedNodes * 25;
  if (runningNode) {
    return Math.min(99, baseFromDone + Math.round((progressPercent(runningNode) / 100) * 25));
  }
  return Math.min(100, baseFromDone);
});

const overallLabel = computed(() => {
  if (!status.value) return "尚未开始";
  if (status.value === "completed") return "全部完成";
  if (status.value === "failed") return "执行失败";
  if (status.value === "completed_with_errors") return "部分完成";
  return `执行中… ${overallProgress.value}%`;
});

const totalElapsedSec = computed(() => {
  if (taskStartedAt.value === null) return 0;
  return Math.round((nowTick.value - taskStartedAt.value) / 1000);
});

// 剩余时间 = 当前 running 节点的 eta_seconds 之和 + 后续未跑节点的预估（用 done 节点平均 * 默认 6s 兜底）
const totalEtaSec = computed(() => {
  const runningStep = steps.value.find((s) => s.status === "running");
  if (runningStep) {
    return runningStep.eta_seconds || 0;
  }
  return 0;
});

onMounted(async () => {
  if (store.taskId) {
    await poll(store.taskId);
    if (status.value && !isFinished(status.value)) {
      pollTimer = window.setInterval(() => poll(store.taskId!), 1800);
    }
  }
  // 即使没任务，也启动秒表，避免进入页面时 elapsed 一直为 0
  if (!tickTimer) {
    tickTimer = window.setInterval(() => (nowTick.value = Date.now()), 1000);
  }
});
onUnmounted(stopPoll);
</script>

<template>
  <div class="page">
    <div class="page-title">Agent 执行流程</div>
    <div class="page-sub">点击开始后，LangGraph 会依次执行 4 个 Agent，下方实时显示每一步状态、当前正在处理哪一条与剩余时间。</div>

    <el-alert
      v-if="connError && running"
      type="warning"
      :closable="false"
      show-icon
      title="与后端连接异常，正在自动重试…"
      style="margin-bottom: 12px"
    />
    <div class="card toolbar">
      <div>
        当前简历：<b>{{ store.resumeName || "未选择" }}</b>
        <el-tag v-if="status" style="margin-left: 12px" :type="TASK_STATUS_META[status]?.type || 'primary'">
          {{ TASK_STATUS_META[status]?.label || status }}
        </el-tag>
      </div>
      <div class="toolbar-stats" v-if="running && !isFinished(status)">
        <div class="stat">
          <span class="stat-label">已耗时</span>
          <span class="stat-value">{{ fmtDuration(totalElapsedSec) }}</span>
        </div>
        <div class="stat" v-if="!isFinished(status)">
          <span class="stat-label">预计剩余</span>
          <span class="stat-value">{{ fmtDuration(totalEtaSec) }}</span>
        </div>
      </div>
      <div style="display: flex; gap: 12px">
        <el-button type="primary" :loading="running" @click="start">开始分析</el-button>
        <el-button
          v-if="running && !isFinished(status)"
          type="danger"
          plain
          :loading="aborting"
          @click="abort"
        >
          中断任务
        </el-button>
        <el-button :disabled="!isFinished(status)" @click="router.push('/results')">
          查看推荐结果 →
        </el-button>
      </div>
    </div>

    <div v-if="steps.length" class="card overall">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px">
        <span style="font-weight: 600">总体进度</span>
        <span style="font-size: 13px; color: #5a6472">{{ overallLabel }}</span>
      </div>
      <el-progress
        :percentage="overallProgress"
        :stroke-width="14"
        :status="status === 'completed' ? 'success' : status === 'failed' ? 'exception' : undefined"
        :indeterminate="status === 'running' && overallProgress === 0"
      />
    </div>

    <div class="timeline">
      <div v-for="(s, i) in steps" :key="s.id" class="tl-item">
        <div class="tl-left">
          <div :class="['tl-dot', { pulse: s.status === 'running' }]" :style="{ background: STATUS_META[s.status]?.color }">
            {{ STATUS_META[s.status]?.icon }}
          </div>
          <div v-if="i < steps.length - 1" class="tl-line" />
        </div>
        <div class="card tl-body">
          <div class="tl-head">
            <b>{{ s.agent_name }}</b>
            <div style="display: flex; gap: 8px; align-items: center">
              <span v-if="s.status === 'running' && s.current_item" class="tl-current">
                {{ s.current_item }}
              </span>
              <el-tag size="small" :color="STATUS_META[s.status]?.color" style="color: #fff; border: none">
                {{ STATUS_META[s.status]?.label }}
              </el-tag>
            </div>
          </div>
          <div class="tl-progress">
            <el-progress
              :percentage="progressPercent(s)"
              :stroke-width="10"
              :status="s.status === 'success' ? 'success' : s.status === 'failed' ? 'exception' : undefined"
              :indeterminate="s.status === 'running' && (s.progress || 0) === 0"
            />
          </div>
          <div class="tl-meta">
            <span>已耗时 {{ fmtDuration(stepElapsedMs(s) / 1000) }}</span>
            <span v-if="s.status === 'running' && s.eta_seconds > 0">
              · 预计剩余 {{ fmtDuration(s.eta_seconds) }}
            </span>
            <span v-if="s.status === 'success' && s.finished_at && s.started_at">
              · 共 {{ fmtDuration((parseBackendTime(s.finished_at) - parseBackendTime(s.started_at)) / 1000) }}
            </span>
          </div>
          <div class="tl-summary">{{ s.summary || "—" }}</div>
          <div v-if="s.error_message" class="tl-error">错误：{{ s.error_message }}</div>
          <el-collapse v-if="s.output_json">
            <el-collapse-item title="查看完整输出">
              <pre class="tl-json">{{ fmt(s.output_json) }}</pre>
            </el-collapse-item>
          </el-collapse>
        </div>
      </div>

      <el-empty v-if="steps.length === 0" description="尚未开始，点击「开始分析」" />
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.toolbar-stats {
  display: flex;
  gap: 18px;
  padding: 6px 14px;
  background: #f5f7fb;
  border-radius: 10px;
}
.stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  line-height: 1.3;
}
.stat-label {
  font-size: 11px;
  color: #8a94a6;
}
.stat-value {
  font-weight: 700;
  font-size: 18px;
  color: #3a6ff7;
  font-variant-numeric: tabular-nums;
}
.overall {
  margin-top: 16px;
}
.timeline {
  margin-top: 6px;
}
.tl-item {
  display: flex;
  gap: 16px;
}
.tl-left {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.tl-dot {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  color: #fff;
  display: grid;
  place-items: center;
  font-size: 16px;
  flex-shrink: 0;
  transition: transform 0.2s;
}
.tl-dot.pulse {
  animation: pulse-ring 1.6s ease-in-out infinite;
  box-shadow: 0 0 0 0 rgba(58, 111, 247, 0.6);
}
@keyframes pulse-ring {
  0% {
    box-shadow: 0 0 0 0 rgba(58, 111, 247, 0.55);
    transform: scale(1);
  }
  70% {
    box-shadow: 0 0 0 12px rgba(58, 111, 247, 0);
    transform: scale(1.05);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(58, 111, 247, 0);
    transform: scale(1);
  }
}
.tl-line {
  width: 2px;
  flex: 1;
  background: #e3e8f0;
  margin: 4px 0;
}
.tl-body {
  flex: 1;
  margin-bottom: 14px;
}
.tl-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.tl-current {
  color: #3a6ff7;
  font-size: 12px;
  background: #eef3ff;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
}
.tl-progress {
  margin-top: 10px;
}
.tl-meta {
  color: #8a94a6;
  font-size: 12px;
  margin-top: 6px;
  display: flex;
  gap: 6px;
}
.tl-summary {
  color: #5a6472;
  margin-top: 8px;
  font-size: 14px;
}
.tl-error {
  color: #e64545;
  font-size: 13px;
  margin-top: 6px;
}
.tl-json {
  background: #f5f7fb;
  padding: 12px;
  border-radius: 8px;
  font-size: 12px;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
}
</style>
