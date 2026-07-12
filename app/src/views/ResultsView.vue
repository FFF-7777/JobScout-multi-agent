<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, type MatchResult } from "@/api";
import { useAppStore } from "@/stores/app";

const router = useRouter();
const store = useAppStore();

const results = ref<MatchResult[]>([]);
const loading = ref(false);
const cityFilter = ref<string>("");
const levelFilter = ref<string>("");
const skillFilter = ref<string>("");

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
        @row-click="(row: MatchResult) => router.push(`/jobs/${row.job_id}`)"
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
            <el-button link type="primary" @click.stop="router.push(`/jobs/${row.job_id}`)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.filters {
  display: flex;
  gap: 12px;
  align-items: center;
}
</style>
