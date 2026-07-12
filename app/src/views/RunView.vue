<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, type AgentRun } from "@/api";
import { useAppStore } from "@/stores/app";

const router = useRouter();
const store = useAppStore();

const steps = ref<AgentRun[]>([]);
const status = ref<string>("");
const running = ref(false);
const connError = ref(false);
let timer: number | null = null;

const STATUS_META: Record<string, { icon: string; color: string; label: string; type: any }> = {
  pending: { icon: "○", color: "#b8bfca", label: "等待中", type: "info" },
  running: { icon: "◔", color: "#3a6ff7", label: "执行中", type: "primary" },
  success: { icon: "✓", color: "#2fae5f", label: "完成", type: "success" },
  failed: { icon: "✕", color: "#e64545", label: "失败", type: "danger" },
};

// 整体任务状态（与后端 _task_status 对齐）的中文标签
const TASK_STATUS_META: Record<string, { label: string; type: any }> = {
  running: { label: "执行中", type: "primary" },
  completed: { label: "全部完成", type: "success" },
  completed_with_errors: { label: "部分完成", type: "warning" },
  failed: { label: "执行失败", type: "danger" },
};

function stopPoll() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
}

function isFinished(status: string) {
  return ["completed", "failed", "completed_with_errors"].includes(status);
}

async function poll(taskId: string) {
  try {
    const t = await api.getTask(taskId);
    steps.value = t.steps;
    status.value = t.status;
    if (isFinished(t.status)) {
      running.value = false;
      stopPoll();
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
  try {
    const jobs = await api.listJobs();
    if (!jobs.length) {
      running.value = false;
      ElMessage.warning("请先导入岗位再开始分析");
      router.push("/jobs");
      return;
    }
    const t = await api.runAgents(store.resumeId, jobs.map((j) => j.id));
    store.setTask(t.task_id);
    steps.value = t.steps;
    timer = window.setInterval(() => poll(t.task_id), 1800);
  } catch (e: any) {
    running.value = false;
    ElMessage.error(e?.response?.data?.detail || "启动失败");
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

const overallProgress = computed(() => {
  if (!steps.value.length) return 0;
  const total = steps.value.reduce((sum, s) => sum + progressPercent(s), 0);
  return Math.round(total / steps.value.length);
});

const overallLabel = computed(() => {
  if (!status.value) return "尚未开始";
  if (status.value === "completed") return "全部完成";
  if (status.value === "failed") return "执行失败";
  if (status.value === "completed_with_errors") return "部分完成";
  return `执行中… ${overallProgress.value}%`;
});

onMounted(async () => {
  if (store.taskId) {
    await poll(store.taskId);
    if (status.value && !isFinished(status.value)) {
      timer = window.setInterval(() => poll(store.taskId!), 1800);
    }
  }
});
onUnmounted(stopPoll);
</script>

<template>
  <div class="page">
    <div class="page-title">Agent 执行流程</div>
    <div class="page-sub">点击开始后，LangGraph 会依次执行 4 个 Agent，下方实时显示每一步状态与进度。</div>

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
      <div style="display: flex; gap: 12px">
        <el-button type="primary" :loading="running" @click="start">开始分析</el-button>
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
          <div class="tl-dot" :style="{ background: STATUS_META[s.status]?.color }">
            {{ STATUS_META[s.status]?.icon }}
          </div>
          <div v-if="i < steps.length - 1" class="tl-line" />
        </div>
        <div class="card tl-body">
          <div class="tl-head">
            <b>{{ s.agent_name }}</b>
            <el-tag size="small" :color="STATUS_META[s.status]?.color" style="color: #fff; border: none">
              {{ STATUS_META[s.status]?.label }}
            </el-tag>
          </div>
          <div class="tl-progress">
            <el-progress
              :percentage="progressPercent(s)"
              :stroke-width="10"
              :status="s.status === 'success' ? 'success' : s.status === 'failed' ? 'exception' : undefined"
              :indeterminate="s.status === 'running' && (s.progress || 0) === 0"
            />
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
}
.tl-progress {
  margin-top: 10px;
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
