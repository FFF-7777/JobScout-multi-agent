<script setup lang="ts">
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { api, type ReportItem } from "@/api";

const reports = ref<ReportItem[]>([]);
const current = ref<ReportItem | null>(null);
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    reports.value = await api.listReports();
    if (reports.value.length && !current.value) {
      current.value = reports.value[0];
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

onMounted(load);
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
        <pre class="md">{{ current.markdown_content }}</pre>
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
.pv-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.md {
  white-space: pre-wrap;
  background: #f5f7fb;
  padding: 16px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.7;
  max-height: 70vh;
  overflow: auto;
}
@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
</style>
