<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { marked } from "marked";
import { api, type ReportItem, type ReportTaskItem } from "@/api";

const reports = ref<ReportItem[]>([]);
const activeTasks = ref<ReportTaskItem[]>([]);
const current = ref<ReportItem | null>(null);
const loading = ref(false);
let autoReload: number | null = null;

const totalCards = computed(() => reports.value.length + activeTasks.value.length);
const initialLoading = computed(() => loading.value && totalCards.value === 0);

function renderMd(text: string): string {
  return marked.parse(text || "", { breaks: true }) as string;
}

async function load() {
  loading.value = true;
  try {
    const [list, tasks] = await Promise.all([
      api.listReports(),
      api.listActiveReportTasks(),
    ]);
    reports.value = list;
    activeTasks.value = tasks;
    if (current.value) {
      current.value = list.find((item) => item.id === current.value?.id) ?? (list[0] ?? null);
    } else {
      current.value = list[0] ?? null;
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "加载报告失败");
  } finally {
    loading.value = false;
  }
}

function open(r: ReportItem) {
  current.value = r;
}

function reportType(r: ReportItem): "deep" | "standard" {
  return r.mode === "deep" ? "deep" : "standard";
}

function reportTypeLabel(r: ReportItem): string {
  return reportType(r) === "deep" ? "深度分析" : "基础分析";
}

function taskTypeLabel(task: ReportTaskItem): string {
  return task.mode === "deep" ? "深度分析" : "基础分析";
}

function taskProgress(task: ReportTaskItem): number {
  if (!task.total) return 0;
  return Math.max(3, Math.min(99, Math.round(((task.done + task.failed) / task.total) * 100)));
}

function taskStatusLabel(task: ReportTaskItem): string {
  if (task.status === "running") return "正在生成";
  if (task.status === "queued") return "排队中";
  return "处理中";
}

function taskSummary(task: ReportTaskItem): string {
  const currentItem = task.current_item ? `当前：${task.current_item}` : "后台正在准备报告内容";
  const progress = `已完成 ${task.done}/${task.total}` + (task.failed ? `，失败 ${task.failed}` : "");
  return `${currentItem} · ${progress}`;
}

function formatDate(value?: string | null): string {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function dlMd(id: number) {
  window.open(api.markdownUrl(id), "_blank");
}

function dlXlsx(id: number) {
  window.open(api.excelUrl(id), "_blank");
}

async function deleteReport(r: ReportItem, e: Event) {
  e.stopPropagation();
  try {
    await ElMessageBox.confirm(
      `确定删除报告“${r.title}”吗？此操作不可恢复。`,
      "删除报告",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" }
    );
  } catch {
    return;
  }
  try {
    await api.deleteReport(r.id);
    ElMessage.success("已删除");
    if (current.value?.id === r.id) {
      current.value = null;
    }
    await load();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "删除失败");
  }
}

onMounted(() => {
  load();
  autoReload = window.setInterval(load, 3000);
});

onUnmounted(() => {
  if (autoReload !== null) {
    clearInterval(autoReload);
    autoReload = null;
  }
});
</script>

<template>
  <div class="page">
    <div class="page-title">报告导出</div>
    <div class="page-sub">
      基础分析与深度分析分别保存。正在生成中的报告也会先显示占位卡，方便你确认任务状态。
    </div>

    <div class="report-shell">
      <aside class="card list soft-scroll">
        <div class="list-head">
          <div>
            <div class="eyebrow">History</div>
            <div class="section-h list-title">历史报告</div>
            <div class="list-count">{{ totalCards }} 个卡片 · {{ reports.length }} 份已完成报告</div>
          </div>
        </div>

        <template v-if="initialLoading">
          <div class="report-skeleton-item" v-for="n in 4" :key="n">
            <div class="report-skeleton-meta">
              <span class="report-skeleton-pill"></span>
              <span class="report-skeleton-date"></span>
            </div>
            <div class="report-skeleton-title"></div>
            <div class="report-skeleton-sub"></div>
          </div>
        </template>

        <section v-else-if="activeTasks.length" class="pending-block">
          <div class="pending-head">
            <span class="pending-dot"></span>
            <span>生成中</span>
            <span class="pending-count">{{ activeTasks.length }}</span>
          </div>

          <div v-for="task in activeTasks" :key="task.task_id" class="report-item pending">
            <div class="ri-meta">
              <span :class="['report-type', task.mode === 'deep' ? 'deep' : 'standard']">
                {{ taskTypeLabel(task) }}
              </span>
              <span class="task-status">{{ taskStatusLabel(task) }}</span>
              <span>{{ formatDate(task.created_at) }}</span>
            </div>
            <div class="ri-title">报告生成中…</div>
            <div class="ri-sub">{{ taskSummary(task) }}</div>
            <el-progress
              class="task-progress"
              :percentage="taskProgress(task)"
              :stroke-width="6"
              :show-text="false"
              :indeterminate="task.status === 'queued'"
              :duration="4"
            />
          </div>
        </section>

        <div
          v-for="r in reports"
          :key="r.id"
          :class="['report-item', { on: current?.id === r.id }]"
          @click="open(r)"
        >
          <div class="ri-meta">
            <span :class="['report-type', reportType(r)]">{{ reportTypeLabel(r) }}</span>
            <span>{{ formatDate(r.created_at) }}</span>
          </div>
          <div class="ri-title">{{ r.title }}</div>
          <div class="ri-sub">{{ r.summary }}</div>
          <button class="ri-del" type="button" title="删除报告" aria-label="删除报告" @click="deleteReport(r, $event)">×</button>
        </div>

        <el-empty v-if="!loading && totalCards === 0" description="暂无报告" />
      </aside>

      <main v-if="current" class="card preview">
        <div class="pv-head">
          <div class="pv-identity">
            <span :class="['report-type', reportType(current)]">{{ reportTypeLabel(current) }}</span>
            <div>
              <h2>{{ current.title }}</h2>
              <p>{{ current.summary }}</p>
            </div>
          </div>
          <div class="pv-actions">
            <el-button @click="dlMd(current.id)">导出 Markdown</el-button>
            <el-button type="primary" @click="dlXlsx(current.id)">导出 Excel</el-button>
          </div>
        </div>
        <article class="md soft-scroll" v-html="renderMd(current.markdown_content)"></article>
      </main>

      <main v-else-if="initialLoading" class="card preview preview-skeleton">
        <div class="preview-skeleton-head">
          <span class="report-skeleton-pill wide"></span>
          <div class="preview-skeleton-title"></div>
          <div class="preview-skeleton-sub"></div>
        </div>
        <div class="preview-skeleton-body">
          <div class="preview-skeleton-line w-100"></div>
          <div class="preview-skeleton-line w-72"></div>
          <div class="preview-skeleton-line w-88"></div>
          <div class="preview-skeleton-line w-64"></div>
          <div class="preview-skeleton-block"></div>
        </div>
      </main>

      <main v-else class="card preview empty-preview">
        <el-empty
          :description="activeTasks.length ? '左侧可查看报告生成进度，完成后会自动出现在历史报告中' : '选择左侧报告查看内容'"
        />
      </main>
    </div>
  </div>
</template>

<style scoped>
.report-shell {
  display: grid;
  grid-template-columns: 340px minmax(0, 1fr);
  gap: 22px;
  align-items: start;
}

.list {
  position: sticky;
  top: 20px;
  max-height: calc(100vh - 48px);
  overflow-y: auto;
  padding: 16px;
}

.list-head {
  padding: 4px 4px 14px;
}

.list-title {
  margin: 14px 0 0;
  font-size: 22px;
}

.list-count {
  margin-top: 8px;
  color: #79859d;
  font-size: 13px;
}

.pending-block {
  margin-bottom: 16px;
}

.report-skeleton-item,
.report-skeleton-pill,
.report-skeleton-date,
.report-skeleton-title,
.report-skeleton-sub,
.preview-skeleton-title,
.preview-skeleton-sub,
.preview-skeleton-line,
.preview-skeleton-block {
  position: relative;
  overflow: hidden;
  background: linear-gradient(90deg, rgba(236, 241, 248, 0.96), rgba(247, 249, 252, 0.98), rgba(236, 241, 248, 0.96));
}

.report-skeleton-item::after,
.report-skeleton-pill::after,
.report-skeleton-date::after,
.report-skeleton-title::after,
.report-skeleton-sub::after,
.preview-skeleton-title::after,
.preview-skeleton-sub::after,
.preview-skeleton-line::after,
.preview-skeleton-block::after {
  content: "";
  position: absolute;
  inset: 0;
  transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.72), transparent);
  animation: report-skeleton-shimmer 1.55s infinite;
}

.report-skeleton-item {
  margin-bottom: 12px;
  padding: 16px;
  border-radius: 18px;
}

.report-skeleton-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.report-skeleton-pill {
  display: inline-block;
  width: 74px;
  height: 24px;
  border-radius: 999px;
}

.report-skeleton-pill.wide {
  width: 96px;
}

.report-skeleton-date {
  width: 72px;
  height: 14px;
  border-radius: 999px;
}

.report-skeleton-title,
.preview-skeleton-title {
  margin-top: 12px;
  height: 26px;
  border-radius: 14px;
}

.report-skeleton-sub,
.preview-skeleton-sub {
  margin-top: 10px;
  height: 14px;
  border-radius: 999px;
}

.preview-skeleton {
  padding: 24px 26px 28px;
}

.preview-skeleton-body {
  margin-top: 24px;
}

.preview-skeleton-line {
  height: 16px;
  margin-bottom: 12px;
  border-radius: 999px;
}

.preview-skeleton-line.w-100 { width: 100%; }
.preview-skeleton-line.w-88 { width: 88%; }
.preview-skeleton-line.w-72 { width: 72%; }
.preview-skeleton-line.w-64 { width: 64%; }

.preview-skeleton-block {
  margin-top: 18px;
  height: 260px;
  border-radius: 20px;
}

@keyframes report-skeleton-shimmer {
  100% {
    transform: translateX(100%);
  }
}

.pending-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 4px 10px;
  color: #5d6c84;
  font-size: 12px;
  font-weight: 700;
}

.pending-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: linear-gradient(135deg, #638bff, #8eb4ff);
  box-shadow: 0 0 0 6px rgba(99, 139, 255, 0.12);
}

.pending-count {
  margin-left: auto;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(228, 237, 255, 0.96);
  color: #4e71d8;
}

.preview {
  padding: 0;
  overflow: hidden;
}

.report-item {
  position: relative;
  margin-bottom: 10px;
  padding: 16px 40px 16px 16px;
  border-radius: 18px;
  border: 1px solid transparent;
  background: rgba(255, 255, 255, 0.38);
  cursor: pointer;
  transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease, box-shadow 0.18s ease;
}

.report-item:hover {
  transform: translateY(-1px);
  border-color: rgba(171, 186, 217, 0.28);
  background: rgba(255, 255, 255, 0.74);
  box-shadow: 0 10px 24px rgba(112, 128, 159, 0.09);
}

.report-item.on {
  border-color: rgba(98, 125, 248, 0.22);
  background: linear-gradient(135deg, rgba(106, 140, 255, 0.12), rgba(255, 255, 255, 0.88));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.62), 0 12px 30px rgba(101, 122, 172, 0.12);
}

.report-item.pending {
  padding-right: 16px;
  cursor: default;
  border-color: rgba(116, 143, 255, 0.18);
  background: linear-gradient(135deg, rgba(228, 238, 255, 0.72), rgba(255, 255, 255, 0.92));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7), 0 14px 30px rgba(108, 136, 196, 0.1);
}

.report-item.pending:hover {
  transform: none;
}

.ri-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  color: #91a0b6;
  font-size: 11px;
}

.report-type {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 800;
  white-space: nowrap;
}

.report-type.standard {
  background: rgba(234, 239, 247, 0.96);
  color: #526071;
}

.report-type.deep {
  background: rgba(232, 240, 255, 0.96);
  color: #2857c5;
}

.task-status {
  color: #5f7ee3;
  font-weight: 700;
}

.ri-title {
  color: #17243b;
  font-weight: 720;
  font-size: 15px;
  line-height: 1.5;
}

.ri-sub {
  margin-top: 6px;
  color: #7f8da4;
  font-size: 12px;
  line-height: 1.7;
}

.task-progress {
  margin-top: 12px;
}

.ri-del {
  position: absolute;
  top: 10px;
  right: 10px;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: #8d99ae;
  font-size: 15px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.18s ease, background 0.18s ease, color 0.18s ease;
}

.report-item:hover .ri-del {
  opacity: 1;
}

.ri-del:hover {
  background: #fff1f1;
  color: #c93636;
}

.pv-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  padding: 24px 26px;
  border-bottom: 1px solid rgba(223, 229, 238, 0.82);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.92), rgba(248, 250, 253, 0.9));
}

.pv-identity {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  min-width: 0;
}

.pv-identity h2 {
  margin: 0;
  color: #17243b;
  font-size: 22px;
  line-height: 1.3;
  letter-spacing: -0.02em;
}

.pv-identity p {
  margin: 6px 0 0;
  color: #8390a6;
  font-size: 13px;
  line-height: 1.65;
}

.pv-actions {
  display: flex;
  gap: 10px;
  flex-shrink: 0;
}

.empty-preview {
  min-height: 420px;
  display: grid;
  place-items: center;
}

.md {
  max-width: 960px;
  max-height: calc(100vh - 190px);
  margin: 0 auto;
  padding: 48px 58px 78px;
  overflow: auto;
  background:
    radial-gradient(circle at top right, rgba(226, 238, 255, 0.36), transparent 26%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(252, 253, 255, 0.96));
  color: #263142;
  font-size: 15px;
  line-height: 1.82;
}

.md :deep(h1) {
  margin: 0 0 12px;
  color: #101828;
  font-size: 34px;
  line-height: 1.16;
  letter-spacing: -0.03em;
}

.md :deep(h2) {
  margin: 42px 0 16px;
  color: #172033;
  font-size: 24px;
  line-height: 1.32;
  letter-spacing: -0.02em;
}

.md :deep(h3) {
  margin: 28px 0 10px;
  color: #273349;
  font-size: 17px;
  line-height: 1.45;
}

.md :deep(p) {
  margin: 10px 0;
}

.md :deep(strong) {
  color: #172033;
  font-weight: 760;
}

.md :deep(ul),
.md :deep(ol) {
  margin: 10px 0 18px;
  padding-left: 22px;
}

.md :deep(li) {
  margin: 8px 0;
  padding-left: 3px;
}

.md :deep(table) {
  border-collapse: separate;
  border-spacing: 0;
  width: 100%;
  margin: 18px 0 26px;
  overflow: hidden;
  border: 1px solid rgba(224, 230, 239, 0.92);
  border-radius: 14px;
  font-size: 13px;
  box-shadow: 0 10px 24px rgba(131, 146, 176, 0.08);
}

.md :deep(th),
.md :deep(td) {
  padding: 11px 13px;
  border-right: 1px solid #e8ecf2;
  border-bottom: 1px solid #e8ecf2;
  text-align: left;
  vertical-align: top;
}

.md :deep(th:last-child),
.md :deep(td:last-child) {
  border-right: 0;
}

.md :deep(tr:last-child td) {
  border-bottom: 0;
}

.md :deep(th) {
  background: #f6f8fb;
  color: #445064;
  font-weight: 760;
}

.md :deep(code) {
  background: #f1f4f8;
  padding: 2px 6px;
  border-radius: 6px;
  font-size: 13px;
}

.md :deep(blockquote) {
  margin: 18px 0 24px;
  padding: 16px 18px;
  border: 1px solid #dce5fa;
  border-left: 3px solid #356ae6;
  border-radius: 12px;
  background: #f6f9ff;
  color: #344563;
}

.md :deep(blockquote p) {
  margin: 0;
}

.md :deep(hr) {
  border: none;
  border-top: 1px solid #e4e8ef;
  margin: 44px 0;
}

@media (max-width: 980px) {
  .report-shell {
    grid-template-columns: 1fr;
  }

  .list {
    position: static;
    max-height: none;
  }

  .pv-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .md {
    max-height: none;
    padding: 34px 22px 56px;
  }
}
</style>
