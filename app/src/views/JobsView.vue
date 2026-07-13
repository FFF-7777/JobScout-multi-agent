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
// 深度分析无数量上限
// #42：导入后后台自动解析，轮询 parse_status 直到全部完成
const parsePollTimer = ref<number | null>(null);

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
  loading.value = true;
  try {
    // 串行设置，避免给后端并发打满；通常选中只有几个，可接受
    for (const id of ids) {
      await api.setJobAnalyzeMode(id, mode);
    }
    ElMessage.success(
      mode === "full"
        ? `已将 ${ids.length} 个岗位切换为深度分析（额外结合简历原文，注意：耗时与 token 成本更高）`
        : `已将 ${ids.length} 个岗位切换为快速分析`
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

// #42：导入后启动轮询，自动解析在后台跑（parse_status: pending/parsing/success/failed）
// 轮询直到没有进行中的岗位为止，避免前端一直刷
function startParsePolling() {
  if (parsePollTimer.value) return;
  parsePollTimer.value = window.setInterval(async () => {
    await refresh();
    const pending = jobs.value.some(
      (j) => j.parse_status === "pending" || j.parse_status === "parsing"
    );
    if (!pending && parsePollTimer.value) {
      clearInterval(parsePollTimer.value);
      parsePollTimer.value = null;
    }
  }, 2000);
}
async function refreshAndWatch() {
  await refresh();
  startParsePolling();
}
function stopParsePolling() {
  if (parsePollTimer.value) {
    clearInterval(parsePollTimer.value);
    parsePollTimer.value = null;
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
    await refreshAndWatch();
    ElMessage.success("岗位已导入，正在后台自动解析");
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
    await refreshAndWatch();
    ElMessage.success(`导入 ${r.length} 个岗位，正在后台自动解析`);
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
    await refreshAndWatch();
    ElMessage.success("链接岗位已导入，正在后台自动解析");
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
    const ok = r.created.length;
    const fail = r.failed.length;
    if (fail === 0) {
      ElMessage.success(`成功识别导入 ${ok} 个岗位`);
    } else {
      const detail = r.failed
        .slice(0, 5)
        .map((f) => `${f.filename}: ${f.error}`)
        .join("；");
      ElMessage.warning(`成功 ${ok} 个，失败 ${fail} 个（${detail}${fail > 5 ? "…" : ""}）`);
    }
    await refreshAndWatch();
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
// 每张待导入图的本地预览 URL（用于缩略图）
const pendingImagePreviews = ref<string[]>([]);

function addPendingImage(file: File) {
  pendingImageFiles.value.push(file);
  pendingImagePreviews.value.push(URL.createObjectURL(file));
}

function removePendingImage(idx: number) {
  // 释放 objectURL，避免内存泄漏
  const url = pendingImagePreviews.value[idx];
  if (url) URL.revokeObjectURL(url);
  pendingImageFiles.value.splice(idx, 1);
  pendingImagePreviews.value.splice(idx, 1);
}

function clearPendingImages() {
  pendingImagePreviews.value.forEach((u) => URL.revokeObjectURL(u));
  pendingImageFiles.value = [];
  pendingImagePreviews.value = [];
}

async function confirmImportImages() {
  if (!pendingImageFiles.value.length) return;
  const files = [...pendingImageFiles.value];
  clearPendingImages();
  await importImages(files);
}

/** 剪贴板粘贴：Ctrl+V 粘贴截图自动收集到待确认队列 */
function handlePaste(e: ClipboardEvent) {
  const items = Array.from(e.clipboardData?.items || []);
  const imageFiles: File[] = [];
  for (const item of items) {
    if (item.type.startsWith("image/")) {
      const file = item.getAsFile();
      if (file) imageFiles.push(file);
    }
  }
  if (!imageFiles.length) return; // 不是图片，让浏览器默认处理（文本粘贴到 textarea 等）
  // 阻止默认行为（避免图片被粘贴到 textarea 里变成 base64 文字）
  e.preventDefault();
  e.stopPropagation();

  // 给粘贴的文件一个友好名字（按序号）
  const named = imageFiles.map(
    (f, i) => new File([f], `clipboard_${Date.now()}_${i + 1}.${f.type.split("/")[1] || "png"}`, { type: f.type })
  );
  named.forEach((f) => addPendingImage(f));

  ElMessage.success(
    `已粘贴 ${named.length} 张图片到待导入队列（共 ${pendingImageFiles.value.length} 张），请点「确认导入」`
  );
}

onMounted(() => {
  refresh();
  document.addEventListener("paste", handlePaste);
});
onBeforeUnmount(() => {
  document.removeEventListener("paste", handlePaste);
  stopParsePolling();
  clearPendingImages();
});

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
          :on-change="(file: any) => { if (file.raw) addPendingImage(file.raw); }"
          accept="image/png,image/jpeg,image/jpg,image/bmp,image/webp"
          multiple
        >
          <el-button :loading="loading">上传图片识别 JD</el-button>
        </el-upload>
        <el-button
          v-if="pendingImageFiles.length > 0"
          type="success"
          :loading="loading"
          @click="confirmImportImages"
        >
          确认导入 ({{ pendingImageFiles.length }} 张)
        </el-button>
        <el-button
          v-if="pendingImageFiles.length > 0"
          plain
          @click="clearPendingImages"
        >
          清空
        </el-button>
        <span class="hint paste-hint">支持 Ctrl+V 直接粘贴截图</span>
        <span class="hint">表格建议列：company_name / job_title / city / salary / jd_text / job_url / source</span>
      </div>

      <!-- 待导入图片预览条：单张右上角 × 按钮 / 一键清空 -->
      <div v-if="pendingImageFiles.length" class="image-preview-bar">
        <div class="preview-label">待导入图片（{{ pendingImageFiles.length }}）：</div>
        <div class="preview-list">
          <div v-for="(url, idx) in pendingImagePreviews" :key="idx" class="preview-item">
            <img :src="url" :alt="pendingImageFiles[idx]?.name" />
            <button class="preview-remove" type="button" title="移除此张" @click="removePendingImage(idx)">
              ×
            </button>
            <div class="preview-name" :title="pendingImageFiles[idx]?.name">
              {{ pendingImageFiles[idx]?.name }}
            </div>
          </div>
        </div>
      </div>

      <div class="url-row">
        <el-input v-model="jobUrl" placeholder="仅支持智联招聘职位链接；BOSS / 拉勾 / 猎聘 / 51job 请改用粘贴 JD 或截图 OCR" />
        <el-button :loading="loading" @click="importUrl">通过链接导入</el-button>
      </div>
      <div class="url-help">
        当前仅稳定支持智联招聘链接导入。BOSS直聘、拉勾、猎聘、51job 常受验证码、登录态或反爬限制影响，建议直接粘贴 JD 或使用截图 OCR 导入。
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
            <el-option label="快速分析" value="summary" />
            <el-option label="深度分析" value="full" />
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
          深度分析：{{ fullModeCount }} 个
          <span class="mode-hint-warn">（无数量上限；额外结合简历原文，单次 LLM 耗时与 token 成本显著增加）</span>
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
        <el-table-column type="selection" width="64" :reserve-selection="true" />
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
              {{ (row.analyze_mode || "summary") === "full" ? "深度" : "快速" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="解析" width="96" align="center">
          <template #default="{ row }">
            <el-tooltip v-if="row.parse_status === 'failed'" :content="row.parse_error || '解析失败'" placement="top">
              <el-tag size="small" type="danger">失败</el-tag>
            </el-tooltip>
            <el-tag v-else-if="row.parse_status === 'success'" size="small" type="success">已解析</el-tag>
            <el-tag v-else-if="row.parse_status === 'parsing'" size="small" type="warning">
              <span class="spin-dot" />解析中
            </el-tag>
            <el-tag v-else size="small" type="info">待解析</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="JD 预览" min-width="360" cell-class-name="jd-prev-cell">
          <template #default="{ row }">
            <div v-if="row.analysis" class="jd-structured">
              <div class="js-row">
                <b>要求：</b>{{ (row.analysis.required_skills || []).join("、") || "—" }}
              </div>
              <div class="js-row">
                <b>优先：</b>{{ (row.analysis.preferred_skills || []).join("、") || "—" }}
              </div>
              <div class="js-row js-indent">
                {{ (row.analysis.responsibilities || []).slice(0, 2).join("；") || "—" }}
              </div>
              <div v-if="(row.analysis.risk_tags || []).length" class="js-risk">
                <el-tag
                  v-for="t in row.analysis.risk_tags"
                  :key="t"
                  size="small"
                  type="danger"
                  effect="plain"
                >{{ t }}</el-tag>
              </div>
            </div>
            <div v-else class="jd-prev">
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
.mode-hint-warn {
  color: #b88218;
}
.hint {
  color: #8a94a6;
  font-size: 12px;
}
.paste-hint {
  color: #3a6ff7;
  font-weight: 500;
}
.url-help {
  margin-top: 10px;
  color: #8a94a6;
  font-size: 12px;
  line-height: 1.7;
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
/* #42：结构化预览（解析完成后展示三栏要点，替代纯 JD 文本） */
.jd-structured {
  font-size: 13px;
  line-height: 1.55;
  color: #2c3340;
}
.jd-structured .js-row {
  margin-bottom: 2px;
}
.jd-structured .js-row b {
  color: #5b667a;
}
.jd-structured .js-indent {
  color: #6b7280;
  padding-left: 2px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.jd-structured .js-risk {
  margin-top: 4px;
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
/* 解析中微动圆点 */
.spin-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #e6a23c;
  margin-right: 4px;
  vertical-align: middle;
  animation: spin-pulse 1s infinite ease-in-out;
}
@keyframes spin-pulse {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 1; }
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

/* === 待导入图片预览条 === */
.image-preview-bar {
  margin-top: 14px;
  padding: 12px;
  background: #f7f9fc;
  border: 1px dashed #c8d2e2;
  border-radius: 8px;
}
.preview-label {
  font-size: 13px;
  color: #5b667a;
  margin-bottom: 8px;
  font-weight: 500;
}
.preview-list {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.preview-item {
  position: relative;
  width: 88px;
  height: 88px;
  border-radius: 6px;
  overflow: hidden;
  background: #fff;
  border: 1px solid #d8dee8;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.preview-item img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.preview-remove {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 50%;
  background: rgba(20, 30, 50, 0.7);
  color: #fff;
  font-size: 14px;
  line-height: 18px;
  cursor: pointer;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s;
}
.preview-remove:hover {
  background: #f56c6c;
}
.preview-name {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  font-size: 10px;
  color: #fff;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.65), rgba(0, 0, 0, 0));
  padding: 12px 4px 3px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;
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
