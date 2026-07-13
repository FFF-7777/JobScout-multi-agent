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
    <div class="page-title">报告导出</div>
    <div class="page-sub">查看历史分析报告，导出 Markdown 或 Excel 岗位表。</div>

    <div class="layout">
      <div class="card list">
        <div class="section-h" style="margin-top: 0">历史报告（{{ reports.length }}）</div>
        <div
          v-for="r in reports"
          :key="r.id"
          :class="['report-item', { on: current?.id === r.id }]"
          @click="open(r)"
        >
          <div class="ri-title">{{ r.title }}</div>
          <div class="ri-sub">{{ r.summary }}</div>
          <button class="ri-del" type="button" title="删除" @click="deleteReport(r, $event)">×</button>
        </div>
        <el-empty v-if="!loading && reports.length === 0" description="暂无报告" />
      </div>

      <div class="card preview" v-if="current">
        <div class="pv-head">
          <b>{{ current.title }}</b>
          <div style="display: flex; gap: 10px">
            <el-button size="small" @click="dlMd(current.id)">导出 Markdown</el-button>
            <el-button size="small" type="primary" @click="dlXlsx(current.id)">导出 Excel</el-button>
          </div>
        </div>
        <div class="md" v-html="renderMd(current.markdown_content)"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 18px;
}
.report-item {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 6px;
}
.report-item:hover {
  background: #f5f7fb;
}
.report-item.on {
  background: #eef3ff;
}
.ri-title {
  font-weight: 600;
  font-size: 14px;
}
.ri-sub {
  color: #8a94a6;
  font-size: 12px;
}
.report-item {
  position: relative;
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
  background: rgba(20, 30, 50, 0.5);
  color: #fff;
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
  background: #f56c6c;
}
.pv-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.md {
  background: #fdfbf7;
  padding: 24px 28px;
  border-radius: 8px;
  font-size: 15px;
  line-height: 1.9;
  max-height: 70vh;
  overflow: auto;
}
.md h1 { font-size: 22px; margin: 0 0 14px; color: #1d2129; }
.md h2 { font-size: 18px; margin: 22px 0 10px; color: #1d2129; }
.md h3 { font-size: 16px; margin: 18px 0 8px; color: #1d2129; }
.md p { margin: 8px 0; }
.md table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 14px; }
.md th, .md td { border: 1px solid #e4dcc8; padding: 8px 12px; text-align: left; }
.md th { background: #f5ecd6; font-weight: 600; }
.md code { background: #ede5d3; padding: 2px 6px; border-radius: 4px; font-size: 14px; }
.md blockquote { border-left: 4px solid #c9a84c; margin: 10px 0; padding: 8px 16px; background: #faf6eb; border-radius: 0 4px 4px 0; }
.md hr { border: none; border-top: 1px solid #e4dcc8; margin: 18px 0; }
@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
</style>
