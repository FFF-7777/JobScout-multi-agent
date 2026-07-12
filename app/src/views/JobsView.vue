<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import type { UploadRequestOptions } from "element-plus";
import { api, type Job } from "@/api";
import { useAppStore } from "@/stores/app";

const router = useRouter();
const store = useAppStore();

const jdText = ref("");
const splitBatch = ref(false);
const jobUrl = ref("");
const loading = ref(false);
const jobs = ref<Job[]>([]);

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
    <div class="page-sub">支持粘贴单个 / 批量 JD、上传 Excel · CSV，或通过岗位链接自动抓取。</div>

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
        <el-button type="primary" @click="startAnalyze">开始分析 →</el-button>
      </div>
      <el-table :data="jobs" style="width: 100%" empty-text="暂无岗位，请先导入">
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
        <el-table-column label="JD 预览" min-width="220">
          <template #default="{ row }">
            <span class="jd-prev">{{ row.jd_text?.slice(0, 40) }}…</span>
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
}
.hint {
  color: #8a94a6;
  font-size: 12px;
}
.jd-prev {
  color: #8a94a6;
  font-size: 13px;
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
