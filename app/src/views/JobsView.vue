<script setup lang="ts">
import { nextTick, onActivated, onBeforeUnmount, onMounted, ref, computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import type { UploadRequestOptions } from "element-plus";
import { api, type Job } from "@/api";
import { useAppStore } from "@/stores/app";

const router = useRouter();
const route = useRoute();
const store = useAppStore();

const SCROLL_KEY = "jobscout.jobs.scrollY";

const jdText = ref("");
const splitBatch = ref(false);
const jobUrl = ref("");
const loading = ref(false);
const jobs = ref<Job[]>([]);
const selectedIds = ref<number[]>([]);
// 行级 loading：行 ID -> 是否在解析
const analyzingIds = ref<Set<number>>(new Set());
// 全文模式上限（与后端 _FULL_MODE_LIMIT 同步）
const FULL_MODE_LIMIT = 10;

const hasSelected = computed(() => selectedIds.value.length > 0);
const selectedCount = computed(() => selectedIds.value.length);
const fullModeCount = computed(
  () => jobs.value.filter((j) => (j.analyze_mode || "summary") === "full").length
);
// 工具栏里那个「分析模式」el-select 的当前值（"summary" | "full"）。
// 没有选中时禁用，值为 "summary" 占位。
const bulkMode = ref<"summary" | "full">("summary");

async function setModeForSelected(mode: "summary" | "full") {
  if (!hasSelected.value) return;
  const ids = [...selectedIds.value];
  // 上限校验：粗略先按后端拒绝的会再回滚
  if (mode === "full") {
    const willFull =
      jobs.value.filter(
        (j) => ids.includes(j.id) && (j.analyze_mode || "summary") !== "full"
      ).length;
    if (fullModeCount.value + willFull > FULL_MODE_LIMIT) {
      ElMessage.warning(
        `全文模式一次最多 ${FULL_MODE_LIMIT} 个岗位（当前已有 ${fullModeCount.value} 个），先把别的切回精简再试`
      );
      return;
    }
  }
  loading.value = true;
  try {
    // 串行设置，避免给后端并发打满；通常选中只有几个，可接受
    for (const id of ids) {
      await api.setJobAnalyzeMode(id, mode);
    }
    ElMessage.success(
      mode === "full"
        ? `已将 ${ids.length} 个岗位切换为全文模式`
        : `已将 ${ids.length} 个岗位切换为精简模式`
    );
    await refresh();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "切换失败");
  } finally {
    loading.value = false;
  }
}

async function refresh() {
  try {
    jobs.value = await api.listJobs();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "加载岗位失败");
  }
}

async function importText() {
  if (!jdText.value.trim()) {
    ElMessage.warning("请粘贴岗位 JD");
    return;
  }
  loading.value = true;
  try {
    await api.importJobsText(jdText.value, splitBatch.value);
    jdText.value = "";
    await refresh();
    ElMessage.success("岗位已导入");
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "导入失败");
  } finally {
    loading.value = false;
  }
}

async function importFile(opt: UploadRequestOptions) {
  loading.value = true;
  try {
    const r = await api.importJobsFile(opt.file);
    await refresh();
    ElMessage.success(`导入 ${r.length} 个岗位`);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "导入失败");
  } finally {
    loading.value = false;
  }
}

async function importUrl() {
  const url = jobUrl.value.trim();
  if (!url) {
    ElMessage.warning("请输入岗位链接");
    return;
  }
  loading.value = true;
  try {
    await api.importJobUrl(url);
    jobUrl.value = "";
    await refresh();
    ElMessage.success("链接岗位已导入");
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "链接导入失败");
  } finally {
    loading.value = false;
  }
}

async function reanalyzeOne(job: Job) {
  analyzingIds.value.add(job.id);
  try {
    const [res] = await api.batchAnalyzeJobs([job.id]);
    if (res?.ok) {
      ElMessage.success(`岗位 ${job.id} 已重新解析`);
    } else {
      ElMessage.error(`岗位 ${job.id} 解析失败：${res?.error || "未知错误"}`);
    }
    await refresh();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "重新解析失败");
  } finally {
    analyzingIds.value.delete(job.id);
  }
}

async function reanalyzeSelected() {
  if (!hasSelected.value) return;
  const ids = [...selectedIds.value];
  loading.value = true;
  try {
    const res = await api.batchAnalyzeJobs(ids);
    const ok = res.filter((r) => r.ok).length;
    const fail = res.length - ok;
    if (fail === 0) {
      ElMessage.success(`已重新解析 ${ok} 个岗位`);
    } else {
      ElMessage.warning(`完成 ${ok} 个，失败 ${fail} 个`);
    }
    await refresh();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "批量重新解析失败");
  } finally {
    loading.value = false;
  }
}

async function deleteOne(job: Job) {
  try {
    await ElMessageBox.confirm(
      `确定删除岗位「${job.company_name || "（无）"} · ${job.job_title || "（无）"}」吗？`,
      "删除岗位",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" }
    );
  } catch {
    return;
  }
  loading.value = true;
  try {
    await api.deleteJob(job.id);
    ElMessage.success("已删除");
    selectedIds.value = selectedIds.value.filter((x) => x !== job.id);
    store.setSelectedJobIds(selectedIds.value);
    await refresh();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "删除失败");
  } finally {
    loading.value = false;
  }
}

async function deleteSelected() {
  if (!hasSelected.value) return;
  const ids = [...selectedIds.value];
  try {
    await ElMessageBox.confirm(
      `确定删除已选中的 ${ids.length} 个岗位吗？`,
      "批量删除",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" }
    );
  } catch {
    return;
  }
  loading.value = true;
  try {
    const res = await api.batchDeleteJobs(ids);
    ElMessage.success(`已删除 ${res.deleted.length} 个岗位`);
    selectedIds.value = [];
    store.setSelectedJobIds([]);
    await refresh();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "批量删除失败");
  } finally {
    loading.value = false;
  }
}

function onSelectionChange(rows: Job[]) {
  selectedIds.value = rows.map((r) => r.id);
  store.setSelectedJobIds(selectedIds.value);
}

function onCellClick(row: Job, _column: any, _cell: any, event: MouseEvent) {
  // 点击 selection 列（checkbox）时不跳详情
  const target = event.target as HTMLElement;
  if (target.closest(".el-table__column--selection") || target.closest(".el-checkbox")) {
    return;
  }
  // 点击操作列时不跳详情
  if (target.closest(".el-table__fixed-right")) {
    return;
  }
  // 记录当前滚动位置，回来时恢复
  const scroller =
    document.querySelector(".el-table .el-scrollbar__wrap") ||
    document.querySelector(".el-table__body-wrapper") ||
    document.scrollingElement;
  if (scroller) {
    sessionStorage.setItem(SCROLL_KEY, String(scroller.scrollTop || 0));
  }
  router.push(`/jobs/${row.id}`);
}

async function restoreScroll() {
  const y = Number(sessionStorage.getItem(SCROLL_KEY) || "0");
  if (!y) return;
  await nextTick();
  // 等 el-table 渲染完
  setTimeout(() => {
    const scroller =
      document.querySelector(".el-table .el-scrollbar__wrap") ||
      document.querySelector(".el-table__body-wrapper") ||
      document.scrollingElement;
    if (scroller) {
      scroller.scrollTop = y;
    }
    sessionStorage.removeItem(SCROLL_KEY);
  }, 50);
}

onMounted(async () => {
  await refresh();
  restoreScroll();
});
// 处理 <keep-alive> 缓存场景（虽然这里没启用，但以防万一）
onActivated(restoreScroll);
// 离开页面前清掉残留
onBeforeUnmount(() => {
  // 仅当不是去 /jobs/:id 时才清（router.push 时我们已经存了新的值，这里兜底）
});

function startAnalyze() {
  if (!store.resumeId) {
    ElMessage.warning("请先在「简历画像」页解析简历");
    router.push("/resume");
    return;
  }
  if (jobs.value.length === 0) {
    ElMessage.warning("请先导入岗位");
    return;
  }
  router.push("/run");
}

onMounted(refresh);
</script>

<template>
  <div class="page">
    <div class="page-title">岗位导入</div>
    <div class="page-sub">支持粘贴单个 / 批量 JD、上传 Excel · CSV，或通过岗位链接自动抓取。勾选后支持单条 / 批量删除与重新解析。</div>

    <div class="card">
      <el-input v-model="jdText" type="textarea" :rows="7" placeholder="粘贴岗位 JD…（批量粘贴可勾选下方拆分）" />
      <div style="margin-top: 12px; display: flex; gap: 14px; align-items: center; flex-wrap: wrap">
        <el-checkbox v-model="splitBatch">按分隔线 / 空行拆分为多个岗位</el-checkbox>
        <el-button type="primary" :loading="loading" @click="importText">导入 JD</el-button>
        <el-upload :show-file-list="false" :http-request="importFile" accept=".xlsx,.xls,.csv">
          <el-button :loading="loading">上传 Excel / CSV</el-button>
        </el-upload>
        <span class="hint">表格建议列：company_name / job_title / city / salary / jd_text / job_url / source</span>
      </div>

      <div class="url-row">
        <el-input v-model="jobUrl" placeholder="粘贴岗位链接，如 BOSS 直聘 / 拉勾 / 智联招聘 JD 页…" />
        <el-button :loading="loading" @click="importUrl">通过链接导入</el-button>
      </div>
    </div>

    <div class="card">
      <div class="toolbar">
        <div class="section-h" style="margin: 0">待分析岗位（{{ jobs.length }}）</div>
        <div class="toolbar-right">
          <span v-if="hasSelected" class="sel-hint">已选 {{ selectedCount }} 个</span>
          <el-select
            :model-value="bulkMode"
            size="default"
            :disabled="!hasSelected"
            :loading="loading"
            style="width: 110px"
            @change="(v: 'summary' | 'full') => { bulkMode = v; setModeForSelected(v); }"
          >
            <el-option label="精简模式" value="summary" />
            <el-option label="全文模式" value="full" />
          </el-select>
          <el-button :disabled="!hasSelected" :loading="loading" @click="reanalyzeSelected">
            批量重新解析
          </el-button>
          <el-button
            type="danger"
            plain
            :disabled="!hasSelected"
            :loading="loading"
            @click="deleteSelected"
          >
            批量删除
          </el-button>
          <el-button
            :type="hasSelected ? 'primary' : 'plain'"
            @click="startAnalyze"
          >
            {{ hasSelected ? `开始分析 (${selectedCount}) →` : "开始分析 →" }}
          </el-button>
        </div>
        <div v-if="fullModeCount > 0" class="mode-hint">
          全文模式：{{ fullModeCount }} / {{ FULL_MODE_LIMIT }}（更精准，单次 LLM 耗时翻倍）
        </div>
      </div>
      <el-table
        :data="jobs"
        style="width: 100%"
        empty-text="暂无岗位，请先导入"
        @selection-change="onSelectionChange"
        @cell-click="onCellClick"
        row-key="id"
        class="job-table"
        :row-style="{ cursor: 'pointer' }"
      >
        <el-table-column type="selection" width="48" />
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="company_name" label="公司" min-width="140">
          <template #default="{ row }">{{ row.company_name || "（待解析）" }}</template>
        </el-table-column>
        <el-table-column prop="job_title" label="岗位" min-width="160">
          <template #default="{ row }">{{ row.job_title || "（待解析）" }}</template>
        </el-table-column>
        <el-table-column prop="city" label="城市" width="90" />
        <el-table-column prop="salary" label="薪资" width="110" />
        <el-table-column prop="source" label="来源" width="90" />
        <el-table-column label="分析模式" width="100">
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="(row.analyze_mode || 'summary') === 'full' ? 'warning' : 'info'"
              effect="light"
            >
              {{ (row.analyze_mode || "summary") === "full" ? "全文" : "精简" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="JD 预览" min-width="380">
          <template #default="{ row }">
            <span class="jd-prev">{{ (row.jd_text || "").slice(0, 100) }}…</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              size="small"
              :loading="analyzingIds.has(row.id)"
              @click.stop="reanalyzeOne(row)"
            >
              重新解析
            </el-button>
            <el-button link type="danger" size="small" @click.stop="deleteOne(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  gap: 12px;
  flex-wrap: wrap;
}
.job-table :deep(.el-table__column--selection) .el-checkbox {
  /* 放大行内 checkbox：从默认 14px 到 18px */
  transform: scale(1.3);
  transform-origin: center;
}
.job-table :deep(.el-table__column--selection) .cell {
  display: flex;
  align-items: center;
  justify-content: center;
}
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.sel-hint {
  color: #3a6ff7;
  font-size: 13px;
  font-weight: 600;
}
.mode-hint {
  color: #b88218;
  font-size: 12px;
  margin-top: 6px;
}
.hint {
  color: #8a94a6;
  font-size: 12px;
}
.jd-prev {
  color: #8a94a6;
  font-size: 13px;
  line-height: 1.5;
  display: inline-block;
  word-break: break-all;
}
.job-table :deep(tbody tr):hover > td {
  background-color: #f5f8ff !important;
}
.url-row {
  display: flex;
  gap: 12px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #ebeef5;
}
.url-row .el-input {
  flex: 1;
}
</style>
