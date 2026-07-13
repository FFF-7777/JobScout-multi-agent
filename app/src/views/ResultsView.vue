<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api, type MatchResult } from "@/api";
import { useAppStore } from "@/stores/app";
import JobDetailView from "@/views/JobDetailView.vue";

const store = useAppStore();

const results = ref<MatchResult[]>([]);
const loading = ref(false);
const cityFilter = ref<string>("");
const levelFilter = ref<string>("");
const decisionFilter = ref<string>("");
const skillFilter = ref<string>("");
const selectedIds = ref<number[]>([]);
const generating = ref(false);
const retrying = ref(false);
const taskStatus = ref<string>("");
// 排序：默认按时间倒序（最新在上）
const sortBy = ref<"time" | "score">("time");

// 分页状态
const page = ref(1);
const pageSize = 30;

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

// ── 决策卡辅助函数 ──
function dj(row: MatchResult): any {
  return row.detail_json ?? {};
}
function decisionAction(row: MatchResult): string {
  return dj(row)?.application_decision?.action ?? "";
}
function decisionLabel(action?: string): string {
  return { priority_apply: "建议投递", apply: "可以投递", selective_apply: "选择性投递", skip: "不建议投递" }[action || ""] || "待评估";
}
function decisionTagType(action?: string): "" | "success" | "warning" | "danger" | "info" {
  return ({ priority_apply: "success", apply: "success", selective_apply: "warning", skip: "danger" }[action || ""] as any) || "info";
}
function topStrengths(row: MatchResult): any[] {
  return dj(row)?.top_strengths ?? [];
}
function mainGaps(row: MatchResult): any[] {
  return dj(row)?.main_gaps ?? [];
}
function appDecision(row: MatchResult): any {
  return dj(row)?.application_decision ?? null;
}
function hrScreening(row: MatchResult): any {
  return dj(row)?.hr_screening ?? null;
}
function careerAlignment(row: MatchResult): any {
  return dj(row)?.career_alignment ?? null;
}
function nextActions(row: MatchResult): any[] {
  return dj(row)?.next_actions ?? [];
}
function hrLabel(result?: string): string {
  return { competitive: "有竞争力", borderline: "存在风险", unlikely: "初筛概率低" }[result || ""] || result || "—";
}

async function loadResults() {
  loading.value = true;
  try {
    const res = await api.listResults({ page_size: 9999 });
    results.value = res.items;
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

function retryOne(row: MatchResult) {
  retryMatch([row.id]);
}

function retryFailedAll() {
  retryMatch([]);
}

const filtered = computed(() =>
  results.value.filter((r) => {
    if (cityFilter.value && r.city !== cityFilter.value) return false;
    if (levelFilter.value && r.level !== levelFilter.value) return false;
    if (decisionFilter.value && decisionAction(r) !== decisionFilter.value) return false;
    if (skillFilter.value) {
      const hay = (r.matched_points || []).join(" ") + r.job_title;
      if (!hay.toLowerCase().includes(skillFilter.value.toLowerCase())) return false;
    }
    return true;
  }).sort((a, b) => {
    if (sortBy.value === "score") {
      // 按分数降序（高分在前）
      return (b.score || 0) - (a.score || 0);
    }
    // 按时间降序（最新在前）：created_at 优先，id 兜底
    const ta = a.created_at ? new Date(a.created_at).getTime() : a.id;
    const tb = b.created_at ? new Date(b.created_at).getTime() : b.id;
    return tb - ta;
  })
);
const totalFiltered = computed(() => filtered.value.length);
const paginated = computed(() => {
  const start = (page.value - 1) * pageSize;
  return filtered.value.slice(start, start + pageSize);
});

// 深度报告后台任务轮询定时器
let deepPollTimer: number | undefined;
function stopDeepPoll() {
  if (deepPollTimer !== undefined) {
    clearInterval(deepPollTimer);
    deepPollTimer = undefined;
  }
}

function _deepTimeout(total: number): number {
  // 每项预估 30s（LLM + 缓存命中可能更快），保底 2min，上限 15min
  const perItem = 30 * 1000;
  const estimated = total * perItem;
  return Math.max(2 * 60 * 1000, Math.min(15 * 60 * 1000, estimated));
}

async function pollDeepTask(taskId: string, total: number) {
  const start = Date.now();
  const timeout = _deepTimeout(total);
  while (Date.now() - start < timeout) {
    await new Promise((r) => setTimeout(r, 1500));
    try {
      const t = await api.getReportTask(taskId);
      if (t.status === "done" || t.status === "partial") {
        const ok = t.done;
        const fail = t.failed;
        ElMessage.success(
          `深度报告生成完成：${ok}/${total} 成功` + (fail ? `（${fail} 失败）` : "")
        );
        stopDeepPoll();
        await loadResults();
        return;
      }
    } catch {
      // 轮询失败不打断
    }
  }
  stopDeepPoll();
  const mins = Math.round(timeout / 60000);
  ElMessage.warning(`深度报告生成超时（>${mins}min），请稍后刷新查看`);
}

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
    if (mode === "deep" && "task_id" in res) {
      ElMessage.info(`深度报告已排队：${res.total_items} 个岗位，后台生成中…`);
      stopDeepPoll();
      await pollDeepTask(res.task_id, res.total_items);
    } else {
      const r = res as { generated: number; errors: any[] };
      ElMessage.success(
        `已生成 ${r.generated} 个基础报告` + (r.errors?.length ? `（${r.errors.length} 个失败）` : "")
      );
      await loadResults();
    }
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
  stopDeepPoll();
});

// 筛选条件变化时重置到第一页
watch([cityFilter, levelFilter, decisionFilter, skillFilter], () => {
  page.value = 1;
  selectedIds.value = [];
});
</script>

<template>
  <div class="page">
    <div class="page-title">岗位推荐结果</div>
    <div class="page-sub">按匹配度排序，支持城市 / 等级 / 投递决策 / 技术栈筛选，点击行展开决策摘要或查看详情。</div>

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
      <el-select v-model="cityFilter" clearable placeholder="城市" style="width: 130px">
        <el-option v-for="c in cities" :key="c" :label="c" :value="c" />
      </el-select>
      <el-select v-model="levelFilter" clearable placeholder="等级" style="width: 100px">
        <el-option v-for="l in ['S', 'A', 'B', 'C', 'D']" :key="l" :label="l" :value="l" />
      </el-select>
      <el-select v-model="decisionFilter" clearable placeholder="投递决策" style="width: 140px">
        <el-option label="建议投递" value="priority_apply" />
        <el-option label="可以投递" value="apply" />
        <el-option label="选择性投递" value="selective_apply" />
        <el-option label="不建议投递" value="skip" />
      </el-select>
      <el-input v-model="skillFilter" clearable placeholder="技术栈关键词" style="width: 180px" />
      <el-radio-group v-model="sortBy" size="small" style="margin-right: 8px">
        <el-radio-button value="time">按时间</el-radio-button>
        <el-radio-button value="score">按分数</el-radio-button>
      </el-radio-group>
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
        :data="paginated"
        style="width: 100%"
        row-key="id"
        empty-text="暂无结果，请先在 Agent 执行页运行分析"
        @row-click="(row: MatchResult) => openDetail(row.job_id)"
        :row-style="{ cursor: 'pointer' }"
        @selection-change="(rows: MatchResult[]) => (selectedIds = rows.map((r) => r.id))"
        :default-sort="{ prop: 'score', order: 'descending' }"
      >
        <el-table-column type="expand" width="36">
          <template #default="{ row }">
            <div class="expand-body" @click.stop>
              <!-- 决策摘要 -->
              <div v-if="appDecision(row)?.summary" class="expand-row">
                <span class="expand-label">决策摘要</span>
                <span class="expand-text">{{ appDecision(row).summary }}</span>
              </div>
              <!-- HR 初筛 + 职业方向 -->
              <div class="expand-row">
                <span class="expand-label">HR 初筛</span>
                <span class="expand-text">
                  {{ hrLabel(hrScreening(row)?.likely_result) }}
                  <span v-if="hrScreening(row)?.main_reason" class="expand-muted">· {{ hrScreening(row).main_reason }}</span>
                </span>
                <span class="expand-label" style="margin-left: 24px">职业方向</span>
                <span class="expand-text">
                  {{ careerAlignment(row)?.score ?? '—' }} 分
                  <span v-if="careerAlignment(row)?.analysis" class="expand-muted">· {{ careerAlignment(row).analysis }}</span>
                </span>
              </div>
              <!-- 核心优势 -->
              <div v-if="topStrengths(row).length" class="expand-section">
                <span class="expand-label" style="color: #2fae5f">✅ 核心优势</span>
                <div v-for="(s, i) in topStrengths(row)" :key="i" class="expand-item">
                  <b>{{ s.title }}</b>
                  <span v-if="s.job_relevance" class="expand-muted"> — {{ s.job_relevance }}</span>
                </div>
              </div>
              <!-- 主要短板 -->
              <div v-if="mainGaps(row).length" class="expand-section">
                <span class="expand-label" style="color: #f7861b">⚠️ 主要短板</span>
                <div v-for="(g, i) in mainGaps(row)" :key="i" class="expand-item">
                  <el-tag size="small"
                    :type="g.severity === 'fatal' ? 'danger' : g.severity === 'major' ? 'warning' : 'info'"
                    effect="dark" style="margin-right: 6px">
                    {{ g.severity === 'fatal' ? '致命' : g.severity === 'major' ? '重要' : '次要' }}
                  </el-tag>
                  <b>{{ g.title }}</b>
                  <span v-if="g.action" class="expand-muted"> — {{ g.action }}</span>
                </div>
              </div>
              <!-- 投递前行动 -->
              <div v-if="nextActions(row).length" class="expand-section">
                <span class="expand-label" style="color: #3a6ff7">📌 投递前行动</span>
                <div v-for="(a, i) in nextActions(row)" :key="i" class="expand-item">
                  <span style="color: #3a6ff7; font-weight: 700">{{ i + 1 }}.</span> {{ a }}
                </div>
              </div>
              <!-- 旧字段兜底：匹配点/缺口 -->
              <div v-if="!topStrengths(row).length && !mainGaps(row).length" class="expand-section">
                <div class="expand-grid">
                  <div>
                    <span class="expand-label" style="color: #2fae5f">✅ 匹配点</span>
                    <ul v-if="row.matched_points?.length">
                      <li v-for="p in row.matched_points" :key="p">{{ p }}</li>
                    </ul>
                    <span v-else class="expand-muted">（无）</span>
                  </div>
                  <div>
                    <span class="expand-label" style="color: #f7861b">⚠️ 缺口</span>
                    <ul v-if="row.missing_points?.length">
                      <li v-for="p in row.missing_points" :key="p">{{ p }}</li>
                    </ul>
                    <span v-else class="expand-muted">（无）</span>
                  </div>
                </div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column type="selection" width="42" />
        <el-table-column prop="company_name" label="公司" min-width="120" />
        <el-table-column prop="job_title" label="岗位" min-width="140" />
        <el-table-column prop="city" label="城市" width="80" />
        <el-table-column prop="salary" label="薪资" width="110" />
        <el-table-column prop="score" label="匹配度" width="100" sortable>
          <template #default="{ row }">
            <el-progress :percentage="row.score" :stroke-width="12" :show-text="false" />
            <span style="font-size: 12px">{{ row.score }}</span>
          </template>
        </el-table-column>
        <el-table-column label="等级" width="70">
          <template #default="{ row }">
            <span :class="['grade-tag', 'grade-' + row.level]">{{ row.level }}</span>
          </template>
        </el-table-column>
        <el-table-column label="投递决策" width="120">
          <template #default="{ row }">
            <el-tag
              v-if="decisionAction(row)"
              :type="decisionTagType(decisionAction(row))"
              size="small"
              effect="dark"
            >
              {{ decisionLabel(decisionAction(row)) }}
            </el-tag>
            <span v-else class="muted">{{ row.recommendation || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="匹配档" width="80">
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
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <template v-if="row.status === 'failed'">
              <el-tag size="small" type="danger">失败</el-tag>
              <el-button
                link
                type="warning"
                size="small"
                :loading="retrying"
                @click.stop="retryOne(row)"
                style="margin-left: 4px"
              >重试</el-button>
              <el-tooltip v-if="row.error_message" :content="row.error_message" placement="top">
                <span class="muted" style="margin-left: 2px; cursor: help">ⓘ</span>
              </el-tooltip>
            </template>
            <template v-else-if="row.status === 'partial'">
              <el-tag size="small" type="warning">部分</el-tag>
              <el-tooltip v-if="row.deep_error_message" :content="row.deep_error_message" placement="top">
                <span class="muted" style="margin-left: 4px; cursor: help">ⓘ</span>
              </el-tooltip>
            </template>
            <el-tag v-else size="small" type="success">正常</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="报告" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.report" :type="row.report.mode === 'deep' ? 'success' : 'info'" size="small">
              {{ row.report.mode === 'deep' ? '深度' : '基础' }}
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="openDetail(row.job_id)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="page-foot">
        <span class="total-hint">共 {{ totalFiltered }} 个岗位</span>
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="totalFiltered"
          layout="prev, pager, next"
          small
          background
        />
      </div>
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
.page-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0 4px;
}
.total-hint {
  color: #8a94a6;
  font-size: 13px;
}

/* === 展开行 === */
.expand-body {
  padding: 16px 24px;
  background: #fafbfd;
  border-radius: 8px;
  margin: 4px 0;
}
.expand-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.expand-label {
  font-size: 13px;
  font-weight: 700;
  color: #5a6472;
  white-space: nowrap;
}
.expand-text {
  font-size: 14px;
  color: #2c3340;
  line-height: 1.6;
}
.expand-muted {
  color: #8a94a6;
  font-size: 13px;
}
.expand-section {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #e0e4ea;
}
.expand-item {
  font-size: 14px;
  color: #2c3340;
  line-height: 1.7;
  margin: 3px 0 3px 16px;
}
.expand-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.expand-grid ul {
  margin: 4px 0;
  padding-left: 20px;
  line-height: 1.8;
  font-size: 14px;
  color: #2c3340;
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
