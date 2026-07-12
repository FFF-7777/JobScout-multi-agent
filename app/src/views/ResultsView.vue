<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, type MatchResult } from "@/api";
import { useAppStore } from "@/stores/app";
import JobDetailView from "@/views/JobDetailView.vue";

const router = useRouter();
const store = useAppStore();

const results = ref<MatchResult[]>([]);
const loading = ref(false);
const cityFilter = ref<string>("");
const levelFilter = ref<string>("");
const skillFilter = ref<string>("");
const selectedIds = ref<number[]>([]);
const generating = ref(false);
const retrying = ref(false);
const taskStatus = ref<string>("");
let pollTimer: number | null = null;

// 全屏大卡 modal 状态（点行弹出，点遮罩关闭）
const detailJobId = ref<number | null>(null);
const detailOpen = ref(false);
function openDetail(jobId: number) {
  detailJobId.value = jobId;
  detailOpen.value = true;
}
function closeDetail() {
  detailOpen.value = false;
  detailJobId.value = null;
}

const matchRunning = computed(
  () => taskStatus.value === "running" || taskStatus.value === ""
);

async function loadResults() {
  if (!store.taskId) return;
  loading.value = true;
  try {
    results.value = await api.listResults(store.taskId);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "加载失败");
  } finally {
    loading.value = false;
  }
}

async function pollTask() {
  if (!store.taskId) return;
  try {
    const t = await api.getTask(store.taskId);
    taskStatus.value = t.status;
  } catch {
    /* 忽略轮询错误 */
  }
}

async function startPolling() {
  await pollTask();
  await loadResults();
  // 匹配还在跑：持续轮询，结果会随 Match Agent 完成而实时增长
  if (taskStatus.value === "running" || taskStatus.value === "") {
    pollTimer = window.setInterval(async () => {
      await pollTask();
      await loadResults();
      if (taskStatus.value !== "running" && taskStatus.value !== "") {
        if (pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
      }
    }, 2500);
  }
}

const cities = computed(() => [...new Set(results.value.map((r) => r.city).filter(Boolean))]);

// P2#14：存在失败项时显示「重试失败项」按钮
const failedCount = computed(
  () => results.value.filter((r) => r.status === "failed").length
);

async function retryMatch(ids: number[]) {
  retrying.value = true;
  try {
    const res = await api.retryMatchResults(ids, store.taskId ?? undefined);
    ElMessage.success(
      `已重试 ${res.generated} 个` + (res.errors.length ? `（${res.errors.length} 个仍失败）` : "")
    );
    await loadResults();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "重试失败");
  } finally {
    retrying.value = false;
  }
}

// 单个失败项重试
function retryOne(row: MatchResult) {
  retryMatch([row.id]);
}

// 批量重试当前任务下所有失败项
function retryFailedAll() {
  retryMatch([]);
}

const filtered = computed(() =>
  results.value.filter((r) => {
    if (cityFilter.value && r.city !== cityFilter.value) return false;
    if (levelFilter.value && r.level !== levelFilter.value) return false;
    if (skillFilter.value) {
      const hay = (r.matched_points || []).join(" ") + r.job_title;
      if (!hay.toLowerCase().includes(skillFilter.value.toLowerCase())) return false;
    }
    return true;
  })
);

async function genReports(mode: "standard" | "deep") {
  const ids = selectedIds.value.length
    ? selectedIds.value
    : results.value.map((r) => r.id);
  if (!ids.length) {
    ElMessage.warning("没有可生成报告的岗位");
    return;
  }
  generating.value = true;
  try {
    const res = await api.generateReports(ids, mode);
    ElMessage.success(
      `已生成 ${res.generated} 个${mode === "deep" ? "深度" : "基础"}报告` +
        (res.errors.length ? `（${res.errors.length} 个失败）` : "")
    );
    await loadResults();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "生成失败");
  } finally {
    generating.value = false;
  }
}

function exportExcel() {
  const rid = store.taskId;
  api
    .listReports()
    .then((reps) => {
      const rep = reps.find((x) => x.task_id === rid) || reps[0];
      if (!rep) {
        ElMessage.warning("当前任务暂无报告，请先运行分析");
        return;
      }
      window.open(api.excelUrl(rep.id), "_blank");
    })
    .catch(() => ElMessage.error("导出失败"));
}

onMounted(startPolling);
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<template>
  <div class="page">
    <div class="page-title">岗位推荐结果</div>
    <div class="page-sub">按匹配度排序，支持城市 / 等级 / 技术栈筛选，点击行查看岗位详情。</div>

    <el-alert
      v-if="matchRunning && results.length > 0"
      type="primary"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
      :title="`匹配进行中，结果持续生成（已完成 ${results.length} 个）`"
      description="下方列表会随 Match Agent 完成自动刷新；报告可在本页按需生成，无需等待全部岗位结束。"
    />

    <div class="card filters">
      <el-select v-model="cityFilter" clearable placeholder="城市" style="width: 140px">
        <el-option v-for="c in cities" :key="c" :label="c" :value="c" />
      </el-select>
      <el-select v-model="levelFilter" clearable placeholder="等级" style="width: 120px">
        <el-option v-for="l in ['S', 'A', 'B', 'C', 'D']" :key="l" :label="l" :value="l" />
      </el-select>
      <el-input v-model="skillFilter" clearable placeholder="技术栈关键词" style="width: 200px" />
      <div style="flex: 1" />
      <span v-if="selectedIds.length" class="sel-hint">已选 {{ selectedIds.length }} 个</span>
      <el-button
        :loading="generating"
        :disabled="results.length === 0"
        @click="genReports('standard')"
      >
        生成基础报告{{ selectedIds.length ? "（选中）" : "（全部）" }}
      </el-button>
      <el-button
        type="primary"
        :loading="generating"
        :disabled="results.length === 0"
        @click="genReports('deep')"
      >
        生成深度报告{{ selectedIds.length ? "（选中）" : "（全部）" }}
      </el-button>
      <el-button
        v-if="failedCount > 0"
        type="warning"
        :loading="retrying"
        @click="retryFailedAll"
      >
        重试失败项（{{ failedCount }}）
      </el-button>
      <el-button :type="results.length > 0 ? 'primary' : 'plain'" @click="exportExcel" :disabled="results.length === 0">
        导出 Excel
      </el-button>
    </div>

    <div class="card">
      <el-table
        v-loading="loading"
        :data="filtered"
        style="width: 100%"
        row-key="id"
        empty-text="暂无结果，请先在 Agent 执行页运行分析"
        @row-click="(row: MatchResult) => openDetail(row.job_id)"
        :row-style="{ cursor: 'pointer' }"
        @selection-change="(rows: MatchResult[]) => (selectedIds = rows.map((r) => r.id))"
        :default-sort="{ prop: 'score', order: 'descending' }"
      >
        <el-table-column type="selection" width="46" />
        <el-table-column prop="company_name" label="公司" min-width="140" />
        <el-table-column prop="job_title" label="岗位" min-width="160" />
        <el-table-column prop="city" label="城市" width="90" />
        <el-table-column prop="salary" label="薪资" width="120" />
        <el-table-column prop="score" label="匹配度" width="110" sortable>
          <template #default="{ row }">
            <el-progress :percentage="row.score" :stroke-width="12" :show-text="false" />
            <span style="font-size: 12px">{{ row.score }}</span>
          </template>
        </el-table-column>
        <el-table-column label="等级" width="80">
          <template #default="{ row }">
            <span :class="['grade-tag', 'grade-' + row.level]">{{ row.level }}</span>
          </template>
        </el-table-column>
        <el-table-column label="匹配档" width="90">
          <template #default="{ row }">
            <el-tag
              v-if="row.match_mode === 'quick'"
              size="small"
              type="info"
            >快速</el-tag>
            <el-tag
              v-else-if="row.match_mode === 'deep'"
              size="small"
              type="warning"
            >深度</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <template v-if="row.status === 'failed'">
              <el-tag size="small" type="danger">失败</el-tag>
              <el-button
                link
                type="warning"
                size="small"
                :loading="retrying"
                @click.stop="retryOne(row)"
                style="margin-left: 6px"
              >重试</el-button>
              <el-tooltip v-if="row.error_message" :content="row.error_message" placement="top">
                <span class="muted" style="margin-left: 4px; cursor: help">ⓘ</span>
              </el-tooltip>
            </template>
            <el-tag v-else size="small" type="success">正常</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="报告" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.report" :type="row.report.mode === 'deep' ? 'success' : 'info'" size="small">
              {{ row.report.mode === 'deep' ? '深度' : '基础' }}
            </el-tag>
            <span v-else class="muted">未生成</span>
          </template>
        </el-table-column>
        <el-table-column prop="recommendation" label="建议" width="120" />
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="openDetail(row.job_id)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 分析结果大卡 modal：点行弹出，点遮罩关闭 -->
    <teleport to="body">
      <transition name="detail-fade">
        <div
          v-if="detailOpen"
          class="detail-mask"
          @mousedown.self="closeDetail"
        >
          <div class="detail-modal" @mousedown.stop>
            <JobDetailView
              v-if="detailJobId !== null"
              :key="detailJobId"
              :job-id-prop="detailJobId"
              :embedded="true"
              mode="analysis"
              @close="closeDetail"
            />
          </div>
        </div>
      </transition>
    </teleport>
  </div>
</template>

<style scoped>
.filters {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.sel-hint {
  font-size: 12px;
  color: #3a6ff7;
}
.muted {
  color: #b8bfca;
  font-size: 12px;
}

/* === 分析结果大卡 modal === */
.detail-mask {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(20, 30, 50, 0.45);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 28px 20px;
  overflow-y: auto;
}
.detail-modal {
  position: relative;
  width: 100%;
  max-width: 980px;
  background: transparent;
  border-radius: 16px;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.25);
  align-self: flex-start;
  margin-bottom: 40px;
}
.detail-modal :deep(.page) {
  max-width: 100%;
  padding: 0;
}
.detail-modal :deep(.back-btn) {
  display: none;
}
.detail-modal :deep(.el-loading-mask) {
  border-radius: 16px;
}
.detail-fade-enter-active,
.detail-fade-leave-active {
  transition: opacity 0.2s ease;
}
.detail-fade-enter-from,
.detail-fade-leave-to {
  opacity: 0;
}
.detail-fade-enter-active .detail-modal,
.detail-fade-leave-active .detail-modal {
  transition: transform 0.22s cubic-bezier(0.2, 0.8, 0.2, 1);
}
.detail-fade-enter-from .detail-modal {
  transform: translateY(20px) scale(0.98);
}
.detail-fade-leave-to .detail-modal {
  transform: translateY(10px) scale(0.99);
}
</style>
