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
  // cancelled 实际不在后端存储里，模板层根据 error_message === "用户中止" 切换
  cancelled: { icon: "∅", color: "#8a94a6", label: "已取消", type: "info" },
};

const TASK_STATUS_META: Record<string, { label: string; type: any }> = {
  running: { label: "执行中", type: "primary" },
  completed: { label: "全部完成", type: "success" },
  completed_with_errors: { label: "部分完成", type: "warning" },
  failed: { label: "执行失败", type: "danger" },
};

/** 后端以 failed + "用户中止" 表达取消；前端在展示层把它当作「已取消」语义。 */
function isCancelled(step: AgentRun): boolean {
  return step.status === "failed" && step.error_message === "用户中止";
}
function stepStatusKey(step: AgentRun): string {
  return isCancelled(step) ? "cancelled" : step.status;
}

/** 任务整体是否被用户取消（即所有 step 都被 abort，没有真失败）。 */
const taskCancelled = computed(() => {
  if (!steps.value.length) return false;
  return steps.value.every(isCancelled);
});

const taskStatusKey = computed(() => {
  if (taskCancelled.value) return "cancelled";
  return status.value;
});

const taskStatusMeta = computed(() => {
  if (taskCancelled.value) return { label: "已取消", type: "info" };
  return TASK_STATUS_META[status.value] ?? { label: status.value || "尚未开始", type: "primary" };
});

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

function reset() {
  // 清空当前任务状态，回到「尚未开始」。后端的历史 agent_runs 保留，store.taskId 清掉即可。
  stopPoll();
  running.value = false;
  status.value = "";
  steps.value = [];
  taskStartedAt.value = null;
  store.setTask("");
  ElMessage.success("已重置任务状态");
}

function fmt(v: any) {
  return JSON.stringify(v, null, 2);
}

function progressPercent(step: AgentRun): number {
  if (step.status === "pending") return 0;
  if (["success", "failed"].includes(step.status)) return 100;
  return step.progress || 0;
}

// 各节点按真实耗时占比加权（Resume 很轻、Match 最重、Report 次之），
// 避免「前 50% 很快、后 50% 等很久」的错觉。
const STEP_WEIGHTS: Record<string, number> = {
  "Resume Agent": 5,
  "Job Agent": 15,
  "Match Agent": 55,
  "Report Agent": 25,
};
// 把 4 个节点按权重拼成一条总进度：节点内 0-100 映射 × 权重
const overallProgress = computed(() => {
  if (!steps.value.length) return 0;
  let acc = 0;
  for (const s of steps.value) {
    const w = STEP_WEIGHTS[s.agent_name] ?? 0;
    acc += (w * progressPercent(s)) / 100;
  }
  return Math.min(100, Math.round(acc));
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

// 关键解耦：只要 Match Agent 已经产出结果（进度>0），就允许用户查看推荐结果，
// 不必等 Report Agent 完成。报告可在结果页按需生成。
const matchStep = computed(() => steps.value.find((s) => s.agent_name === "Match Agent"));
const hasMatchResults = computed(() => (matchStep.value?.progress ?? 0) > 0);

// 剩余时间范围（基于 P50/P90）：用 Match Agent 节点的 eta_low/eta_high
const totalEtaRange = computed(() => {
  const m = matchStep.value;
  if (m && m.status === "running") {
    return { low: m.eta_low, high: m.eta_high };
  }
  return null;
});

function fmtEtaRange(low: number, high: number): string {
  if (!low && !high) return "估算中…";
  const lm = Math.ceil(low / 60);
  const hm = Math.ceil(high / 60);
  if (lm >= 1 || hm >= 1) {
    return lm === hm ? `约 ${lm} 分钟` : `约 ${lm}~${hm} 分钟`;
  }
  return `约 ${Math.max(1, Math.ceil(low))}~${Math.ceil(high)} 秒`;
}

// P2#10：单条执行记录（AgentItemRun）展示，便于排查某岗位为何失败/耗时
const itemRuns = ref<any[]>([]);
const itemRunsOpen = ref(false);
const itemRunsLoading = ref(false);
const failedItemRuns = computed(() => itemRuns.value.filter((r) => r.status === "failed"));
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
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${s % 60}s`;
}

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
        <el-tag v-if="status" style="margin-left: 12px" :type="taskStatusMeta.type">
          {{ taskStatusMeta.label }}
        </el-tag>
      </div>
      <div class="toolbar-stats" v-if="running && !isFinished(status)">
        <div class="stat">
          <span class="stat-label">已耗时</span>
          <span class="stat-value">{{ fmtDuration(totalElapsedSec) }}</span>
        </div>
        <div class="stat" v-if="!isFinished(status)">
          <span class="stat-label">预计剩余</span>
          <span class="stat-value" v-if="totalEtaRange && (totalEtaRange.low > 0 || totalEtaRange.high > 0)">{{ fmtEtaRange(totalEtaRange.low, totalEtaRange.high) }}</span>
          <span class="stat-value" v-else>估算中…</span>
        </div>
      </div>
      <div style="display: flex; gap: 12px">
        <!-- 状态机：
             - 未开始：「开始分析」primary（蓝），「查看推荐」disabled
             - 分析中：「开始分析」loading 灰，「中断任务」danger（红），「查看推荐」disabled
             - 已完成（success / completed_with_errors / cancelled / failed）：
               「重新开始」plain（次要），「查看推荐结果」primary（亮蓝引导下一步） -->
        <el-button
          v-if="!running && !isFinished(status)"
          type="primary"
          :loading="running"
          @click="start"
        >
          开始分析
        </el-button>
        <el-button
          v-else-if="running"
          :loading="true"
          disabled
        >
          开始分析
        </el-button>
        <el-button
          v-else
          plain
          @click="reset"
        >
          重新开始
        </el-button>
        <el-button
          v-if="running && !isFinished(status)"
          type="danger"
          plain
          :loading="aborting"
          @click="abort"
        >
          中断任务
        </el-button>
        <el-button
          type="primary"
          :disabled="!hasMatchResults"
          @click="router.push('/results')"
        >
          查看推荐结果 →
        </el-button>
        <span v-if="running && hasMatchResults" class="running-hint">
          匹配进行中，结果持续生成，可先查看已有结果
        </span>
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
        :status="taskStatusKey === 'completed' ? 'success' : taskStatusKey === 'cancelled' ? 'warning' : taskStatusKey === 'failed' ? 'exception' : undefined"
        :indeterminate="status === 'running' && overallProgress === 0"
      />
    </div>

    <div class="timeline">
      <div v-for="(s, i) in steps" :key="s.id" class="tl-item">
        <div class="tl-left">
          <div :class="['tl-dot', { pulse: s.status === 'running' }]" :style="{ background: STATUS_META[stepStatusKey(s)]?.color }">
            {{ STATUS_META[stepStatusKey(s)]?.icon }}
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
              <el-tag size="small" :color="STATUS_META[stepStatusKey(s)]?.color" style="color: #fff; border: none">
                {{ STATUS_META[stepStatusKey(s)]?.label }}
              </el-tag>
            </div>
          </div>
          <div class="tl-progress">
            <el-progress
              :percentage="progressPercent(s)"
              :stroke-width="10"
              :status="s.status === 'success' ? 'success' : isCancelled(s) ? 'warning' : s.status === 'failed' ? 'exception' : undefined"
              :indeterminate="s.status === 'running' && (s.progress || 0) === 0"
            />
          </div>
          <div class="tl-meta">
            <span>已耗时 {{ fmtDuration(stepElapsedMs(s) / 1000) }}</span>
            <span v-if="s.status === 'running' && (s.eta_low > 0 || s.eta_high > 0)">
              · 预计剩余 {{ fmtEtaRange(s.eta_low, s.eta_high) }}
            </span>
            <span v-else-if="s.status === 'running'">· 预计剩余 估算中…</span>
            <span v-if="s.status === 'success' && s.finished_at && s.started_at">
              · 共 {{ fmtDuration((parseBackendTime(s.finished_at) - parseBackendTime(s.started_at)) / 1000) }}
            </span>
          </div>
          <div class="tl-summary">{{ s.summary || "—" }}</div>
          <template v-if="s.agent_name === 'Match Agent' && s.status === 'running'">
            <div class="tl-counts">
              <span>已完成 <b>{{ s.completed_items }}</b></span>
              <span v-if="s.failed_items > 0" class="bad">失败 <b>{{ s.failed_items }}</b></span>
              <span>排队 <b>{{ Math.max(0, s.total_items - s.completed_items - s.failed_items) }}</b></span>
            </div>
            <el-collapse v-if="s.in_flight_items && s.in_flight_items.length" class="tl-inflight">
              <el-collapse-item :title="`正在并发分析（${s.in_flight_items.length}）`">
                <ul class="inflight-list">
                  <li v-for="it in s.in_flight_items" :key="it.job_id">
                    • {{ it.job_title || ('岗位 ' + it.job_id) }}
                  </li>
                </ul>
              </el-collapse-item>
            </el-collapse>
          </template>
          <div v-if="s.error_message && !isCancelled(s)" class="tl-error">错误：{{ s.error_message }}</div>
          <div v-else-if="isCancelled(s)" class="tl-cancelled">用户已中断此节点</div>
          <template v-if="s.agent_name === 'Match Agent'">
            <div class="tl-itemruns">
              <el-button link type="primary" size="small" :loading="itemRunsLoading" @click="toggleItemRuns">
                执行明细（{{ itemRuns.length || "点击加载" }}<template v-if="failedItemRuns.length"> · {{ failedItemRuns.length }} 个失败</template>）
              </el-button>
              <el-collapse v-if="itemRunsOpen" class="tl-inflight">
                <el-collapse-item title="单条执行记录（档位 / 状态 / 耗时 / 错误）">
                  <table class="ir-table">
                    <thead>
                      <tr><th>岗位</th><th>档位</th><th>状态</th><th>耗时</th><th>错误</th></tr>
                    </thead>
                    <tbody>
                      <tr v-for="r in itemRuns" :key="r.id" :class="{ 'ir-fail': r.status === 'failed' }">
                        <td>{{ r.item_label || ("岗位 " + r.item_id) }}</td>
                        <td>{{ r.tier === "deep" ? "深度" : r.tier === "quick" ? "快速" : "—" }}</td>
                        <td>
                          <el-tag size="small" :type="r.status === 'failed' ? 'danger' : r.status === 'done' ? 'success' : 'info'">
                            {{ r.status === "done" ? "完成" : r.status === "failed" ? "失败" : r.status === "running" ? "运行中" : "排队" }}
                          </el-tag>
                        </td>
                        <td>{{ fmtItemDuration(r.duration_ms) }}</td>
                        <td class="ir-err">{{ r.error_message || "—" }}</td>
                      </tr>
                    </tbody>
                  </table>
                </el-collapse-item>
              </el-collapse>
            </div>
          </template>
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
.running-hint {
  font-size: 12px;
  color: #3a6ff7;
  background: #eef3ff;
  padding: 4px 10px;
  border-radius: 10px;
}
.tl-counts {
  display: flex;
  gap: 14px;
  margin-top: 8px;
  font-size: 12px;
  color: #5a6472;
}
.tl-counts b {
  color: #1f2733;
}
.tl-counts .bad {
  color: #e64545;
}
.tl-inflight {
  margin-top: 6px;
}
.inflight-list {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  color: #5a6472;
  line-height: 1.7;
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
.tl-cancelled {
  color: #8a94a6;
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
/* P2#10：单条执行记录表 */
.tl-itemruns {
  margin-top: 10px;
}
.ir-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.ir-table th,
.ir-table td {
  text-align: left;
  padding: 6px 8px;
  border-bottom: 1px solid #eef1f6;
  vertical-align: top;
}
.ir-table th {
  color: #8a94a6;
  font-weight: 600;
  background: #f7f9fc;
}
.ir-table tr.ir-fail td {
  background: #fff5f5;
}
.ir-table .ir-err {
  color: #e64545;
  max-width: 260px;
  word-break: break-word;
}
</style>
