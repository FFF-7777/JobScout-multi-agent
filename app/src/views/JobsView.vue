<script setup lang="ts">
import { nextTick, onActivated, onBeforeUnmount, onMounted, ref, computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import type { UploadRequestOptions } from "element-plus";
import { api, type Job } from "@/api";
import { useAppStore } from "@/stores/app";
import JobDetailView from "@/views/JobDetailView.vue";

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

async function importImages(uploadedFiles: File[]) {
  if (!uploadedFiles.length) return;
  loading.value = true;
  try {
    const r = await api.importJobImages(uploadedFiles);
    if (r.length === uploadedFiles.length) {
      ElMessage.success(`成功识别导入 ${r.length} 个岗位`);
    } else {
      ElMessage.warning(`成功 ${r.length}/${uploadedFiles.length} 个（部分图片识别失败）`);
    }
    await refresh();
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e?.message || "图片识别失败";
    ElMessage.error(detail);
  } finally {
    loading.value = false;
  }
}

// 拖拽/多选时 el-upload 会逐个触发 http-request，
// 改用 before-upload 收集文件 + 手动按钮触发
const pendingImageFiles = ref<File[]>([]);

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

// 全屏大卡片 modal 状态（点 JD 弹出，点卡片外关闭）
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
// 给 JobDetailView 用的 close
function detailClose() {
  closeDetail();
}

function onCellClick(row: Job, _column: any, _cell: any, event: MouseEvent) {
  // 点击 selection 列（checkbox）时不弹详情
  const target = event.target as HTMLElement;
  if (target.closest(".el-table__column--selection") || target.closest(".el-checkbox")) {
    return;
  }
  // 点击操作列时不弹详情
  if (target.closest(".el-table__fixed-right")) {
    return;
  }
  // 弹全屏大卡片 modal（不走路由）
  openDetail(row.id);
}

async function restoreScroll() {
  const y = Number(sessionStorage.getItem(SCROLL_KEY) || "0");
  if (!y) return;
  // 用 rAF + setTimeout 双保险，等 DOM/图片都画完再滚
  await nextTick();
  requestAnimationFrame(() => {
    window.scrollTo({ top: y, left: 0, behavior: "auto" });
  });
  setTimeout(() => {
    window.scrollTo({ top: y, left: 0, behavior: "auto" });
    sessionStorage.removeItem(SCROLL_KEY);
  }, 200);
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
        <el-upload
          :show-file-list="false"
          :http-request="importFile"
          accept=".xlsx,.xls,.csv"
        >
          <el-button :loading="loading">上传 Excel / CSV</el-button>
        </el-upload>
        <el-upload
          :show-file-list="false"
          :auto-upload="false"
          :on-change="(file: any) => { if (file.raw) pendingImageFiles.push(file.raw); }"
          accept="image/png,image/jpeg,image/jpg,image/bmp,image/webp"
          multiple
        >
          <el-button :loading="loading">上传图片识别 JD</el-button>
        </el-upload>
        <el-button
          v-if="pendingImageFiles.length > 0"
          type="success"
          :loading="loading"
          @click="() => { importImages(pendingImageFiles); pendingImageFiles = []; }"
        >
          确认导入 ({{ pendingImageFiles.length }} 张)
        </el-button>
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
        <el-table-column type="selection" width="64" />
        <el-table-column prop="id" label="ID" width="56" />
        <el-table-column prop="company_name" label="公司" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ row.company_name || "（待解析）" }}</template>
        </el-table-column>
        <el-table-column prop="job_title" label="岗位" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.job_title || "（待解析）" }}</template>
        </el-table-column>
        <el-table-column prop="city" label="城市" width="80" show-overflow-tooltip />
        <el-table-column prop="salary" label="薪资" width="110" show-overflow-tooltip />
        <el-table-column prop="source" label="来源" width="80" show-overflow-tooltip />
        <el-table-column label="模式" width="64" align="center">
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="(row.analyze_mode || 'summary') === 'full' ? 'warning' : 'info'"
              effect="plain"
              style="padding: 0 4px"
            >
              {{ (row.analyze_mode || "summary") === "full" ? "全文" : "精" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="JD 预览" min-width="360" cell-class-name="jd-prev-cell">
          <template #default="{ row }">
            <div class="jd-prev">
              {{ row.jd_text || "（暂无 JD 文本）" }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="center">
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

    <!-- 岗位详情大卡片 modal：覆盖全页，点遮罩（卡片外空白）关闭 -->
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
              @close="closeDetail"
            />
          </div>
        </div>
      </transition>
    </teleport>
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
/* 行内 checkbox 放大样式已挪到全局 app/src/styles.css（用 !important 提高优先级） */
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
  color: #2c3340;
  font-size: 14px;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;            /* 3 行 → 2 行，按用户要求缩小一半 */
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  padding-right: 12px;
  box-sizing: border-box;
}
/* 让 JD 列的 td 上下 padding 大一点、top 对齐 */
.job-table :deep(td.jd-prev-cell),
.job-table :deep(.jd-prev-cell) {
  padding-top: 12px !important;
  padding-bottom: 12px !important;
  padding-right: 18px !important;
  vertical-align: top;
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

/* === 岗位详情全屏大卡 modal === */
.detail-mask {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(20, 30, 50, 0.45);     /* 卡片外的灰色遮罩 */
  backdrop-filter: blur(2px);
  display: flex;
  align-items: flex-start;                /* 顶部对齐，让大卡从顶部开始 */
  justify-content: center;
  padding: 28px 20px;
  overflow-y: auto;
}
.detail-modal {
  position: relative;
  width: 100%;
  max-width: 980px;                       /* 大卡宽度 */
  background: transparent;                /* 大卡背景由 JobDetailView 自带 */
  border-radius: 16px;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.25);
  /* 关键：让 .page 内部自带的 padding 略减小 */
  padding: 0;
  align-self: flex-start;
  margin-bottom: 40px;
}
.detail-modal :deep(.page) {
  max-width: 100%;
  padding: 0;
}
.detail-modal :deep(.back-btn) {
  display: none;                          /* modal 模式下隐藏「← 返回」按钮（用我们的 X） */
}
.detail-modal :deep(.el-loading-mask) {
  border-radius: 16px;
}

/* 进入/退出动画 */
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

/* 让 JD 单元格可点击：cursor 已经在 .jd-prev 设置了 */
</style>
