<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { marked } from "marked";
import { api, type ReportItem } from "@/api";

const reports = ref<ReportItem[]>([]);
const current = ref<ReportItem | null>(null);
const loading = ref(false);
let autoReload: number | null = null;

function renderMd(text: string): string {
  return marked.parse(text || "", { breaks: true }) as string;
}

async function load() {
  loading.value = true;
  try {
    const list = await api.listReports();
    reports.value = list;
    // 保持当前选中的报告不变（若有新报告且无选中，自动切到最新的）
    if (list.length && !current.value) {
      current.value = list[0];
    } else if (!current.value) {
      current.value = null;
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
  return /深度/.test(`${r.title} ${r.summary}`) ? "deep" : "standard";
}
function reportTypeLabel(r: ReportItem): string {
  return reportType(r) === "deep" ? "深度分析" : "基础分析";
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
  // 阻止冒泡触发选中
  e.stopPropagation();
  try {
    await ElMessageBox.confirm(
      `确定删除报告「${r.title}」吗？此操作不可恢复。`,
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
  // 每 30s 自动拉取最新报告列表，让后台生成的报告自动出现
  autoReload = window.setInterval(load, 30000);
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
    <div class="page-title">分析报告</div>
    <div class="page-sub">基础分析与深度分析分别保存，可独立查看和导出。</div>

    <div class="layout">
      <aside class="card list">
        <div class="list-head">
          <div>
            <div class="section-h">历史版本</div>
            <div class="list-count">{{ reports.length }} 份报告</div>
          </div>
        </div>
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
        <el-empty v-if="!loading && reports.length === 0" description="暂无报告" />
      </aside>

      <main class="card preview" v-if="current">
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
        <article class="md" v-html="renderMd(current.markdown_content)"></article>
      </main>
      <main v-else class="card preview empty-preview">
        <el-empty description="选择左侧报告查看内容" />
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}
.list {
  position: sticky;
  top: 20px;
  max-height: calc(100vh - 48px);
  overflow-y: auto;
  padding: 14px;
}
.list-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 6px 14px;
}
.list-head .section-h { margin: 0; font-size: 17px; }
.list-count { margin-top: 2px; color: #87909f; font-size: 12px; }
.preview {
  padding: 0;
  overflow: hidden;
  border: 1px solid #e4e8ef;
  box-shadow: 0 8px 30px rgba(25, 36, 54, 0.06);
}
.report-item {
  position: relative;
  padding: 13px 36px 13px 13px;
  border: 1px solid transparent;
  border-radius: 10px;
  cursor: pointer;
  margin-bottom: 8px;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}
.report-item:hover {
  background: #f8fafc;
  border-color: #e3e8f0;
}
.report-item.on {
  background: #f2f6ff;
  border-color: #cbd9f8;
  box-shadow: inset 3px 0 #356ae6;
}
.ri-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 7px;
  color: #98a1af;
  font-size: 11px;
}
.report-type {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  white-space: nowrap;
}
.report-type.standard { background: #eef2f6; color: #526071; }
.report-type.deep { background: #eaf0ff; color: #2857c5; }
.ri-title {
  color: #172033;
  font-weight: 650;
  font-size: 14px;
  line-height: 1.45;
}
.ri-sub {
  margin-top: 4px;
  color: #87909f;
  font-size: 12px;
  line-height: 1.5;
}
.report-item:hover .ri-del {
  opacity: 1;
}
.ri-del {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: #87909f;
  font-size: 14px;
  line-height: 20px;
  cursor: pointer;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.15s, background 0.15s;
}
.ri-del:hover {
  background: #fff0f0;
  color: #c93636;
}
.pv-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  padding: 18px 22px;
  border-bottom: 1px solid #e8ebf0;
  background: #fff;
}
.pv-identity { display: flex; align-items: flex-start; gap: 12px; min-width: 0; }
.pv-identity h2 { margin: 0; color: #172033; font-size: 16px; line-height: 1.4; }
.pv-identity p { margin: 3px 0 0; color: #87909f; font-size: 12px; }
.pv-actions { display: flex; gap: 10px; flex-shrink: 0; }
.empty-preview { min-height: 420px; display: grid; place-items: center; }
.md {
  max-width: 920px;
  max-height: calc(100vh - 178px);
  margin: 0 auto;
  padding: 46px 56px 72px;
  overflow: auto;
  background: #fff;
  color: #263142;
  font-size: 15px;
  line-height: 1.78;
}
.md :deep(h1) { margin: 0 0 10px; color: #101828; font-size: 30px; line-height: 1.25; letter-spacing: -0.02em; }
.md :deep(h2) { margin: 40px 0 15px; padding-top: 4px; color: #172033; font-size: 21px; line-height: 1.35; }
.md :deep(h3) { margin: 28px 0 10px; color: #273349; font-size: 16px; line-height: 1.4; }
.md :deep(p) { margin: 9px 0; }
.md :deep(strong) { color: #172033; font-weight: 700; }
.md :deep(ul), .md :deep(ol) { margin: 9px 0 18px; padding-left: 22px; }
.md :deep(li) { margin: 7px 0; padding-left: 3px; }
.md :deep(table) { border-collapse: separate; border-spacing: 0; width: 100%; margin: 16px 0 24px; overflow: hidden; border: 1px solid #e3e8ef; border-radius: 9px; font-size: 13px; }
.md :deep(th), .md :deep(td) { padding: 10px 12px; border-right: 1px solid #e8ecf2; border-bottom: 1px solid #e8ecf2; text-align: left; vertical-align: top; }
.md :deep(th:last-child), .md :deep(td:last-child) { border-right: 0; }
.md :deep(tr:last-child td) { border-bottom: 0; }
.md :deep(th) { background: #f6f8fb; color: #445064; font-weight: 700; }
.md :deep(code) { background: #f1f4f8; padding: 2px 5px; border-radius: 4px; font-size: 13px; }
.md :deep(blockquote) { margin: 16px 0 22px; padding: 14px 17px; border: 1px solid #dce5fa; border-left: 3px solid #356ae6; border-radius: 8px; background: #f6f9ff; color: #344563; }
.md :deep(blockquote p) { margin: 0; }
.md :deep(hr) { border: none; border-top: 1px solid #e4e8ef; margin: 42px 0; }
@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .list { position: static; max-height: none; }
  .pv-head { align-items: flex-start; flex-direction: column; }
  .md { max-height: none; padding: 32px 22px 54px; }
}
</style>
