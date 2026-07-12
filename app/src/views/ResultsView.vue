<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
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

async function load() {
  loading.value = true;
  try {
    results.value = await api.listResults(store.taskId || undefined);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "加载失败");
  } finally {
    loading.value = false;
  }
}

const cities = computed(() => [...new Set(results.value.map((r) => r.city).filter(Boolean))]);

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

function exportExcel() {
  const rid = store.taskId;
  api.listReports()
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

onMounted(load);
</script>

<template>
  <div class="page">
    <div class="page-title">岗位推荐结果</div>
    <div class="page-sub">按匹配度排序，支持城市 / 等级 / 技术栈筛选，点击行查看岗位详情。</div>

    <div class="card filters">
      <el-select v-model="cityFilter" clearable placeholder="城市" style="width: 140px">
        <el-option v-for="c in cities" :key="c" :label="c" :value="c" />
      </el-select>
      <el-select v-model="levelFilter" clearable placeholder="等级" style="width: 120px">
        <el-option v-for="l in ['S', 'A', 'B', 'C', 'D']" :key="l" :label="l" :value="l" />
      </el-select>
      <el-input v-model="skillFilter" clearable placeholder="技术栈关键词" style="width: 200px" />
      <div style="flex: 1" />
      <el-button :type="results.length > 0 ? 'primary' : 'plain'" @click="exportExcel" :disabled="results.length === 0">
        导出 Excel
      </el-button>
    </div>

    <div class="card">
      <el-table
        v-loading="loading"
        :data="filtered"
        style="width: 100%"
        empty-text="暂无结果，请先在 Agent 执行页运行分析"
        @row-click="(row: MatchResult) => openDetail(row.job_id)"
        :row-style="{ cursor: 'pointer' }"
        :default-sort="{ prop: 'score', order: 'descending' }"
      >
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
