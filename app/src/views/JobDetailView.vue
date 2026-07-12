<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, type Job } from "@/api";

const props = defineProps<{
  /** 当作为 modal 嵌入时，从父组件传 jobId 覆盖 route.params.id */
  jobIdProp?: number | null;
  /** 当作为 modal 嵌入时，禁用内部跳路由（无效 ID 时不 router.replace） */
  embedded?: boolean;
}>();
const emit = defineEmits<{ (e: "close"): void }>();

const route = useRoute();
const router = useRouter();

function resolveJobId(): number | null {
  if (typeof props.jobIdProp === "number" && props.jobIdProp > 0) return props.jobIdProp;
  const raw = Number(route.params.id);
  return Number.isFinite(raw) && raw > 0 ? raw : null;
}

const job = ref<Job | null>(null);
const loading = ref(true);

async function load(targetId?: number | null) {
  const id = targetId ?? resolveJobId();
  if (id === null) {
    ElMessage.error("无效的岗位 ID");
    if (!props.embedded) {
      router.replace("/jobs");
    } else {
      emit("close");
    }
    return;
  }
  loading.value = true;
  try {
    job.value = await api.getJob(id);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "加载失败");
  } finally {
    loading.value = false;
  }
}

onMounted(() => load());
watch(
  () => props.jobIdProp,
  (newId, oldId) => {
    if (newId !== oldId) load(newId);
  }
);

function goBack() {
  if (props.embedded) {
    emit("close");
  } else {
    router.back();
  }
}

// 跳到推荐结果页（看这个岗位的匹配分析）
function gotoResults() {
  router.push("/results");
}
</script>

<template>
  <div class="page" v-loading="loading">
    <el-button v-if="!embedded" link type="primary" class="back-btn" @click="goBack()">
      ← 返回
    </el-button>
    <el-empty v-if="!loading && !job" description="未找到该岗位（可能已被删除）" />
    <div v-else-if="job" class="big-card">
      <button
        v-if="embedded"
        class="bigcard-close"
        @click="emit('close')"
        title="关闭"
        aria-label="关闭"
      >
        ×
      </button>

      <!-- 头部：岗位名片 -->
      <div class="big-card-head">
        <div class="hero-title">
          <span class="company">{{ job.company_name || "（待解析）" }}</span>
          <span class="dot">·</span>
          <span class="role">{{ job.job_title || "（待解析）" }}</span>
        </div>
        <div class="hero-tags">
          <el-tag v-if="job.city" size="default" effect="plain">📍 {{ job.city }}</el-tag>
          <el-tag v-if="job.salary" size="default" effect="plain" type="success">
            💰 {{ job.salary }}
          </el-tag>
          <el-tag v-if="job.source" size="default" effect="plain" type="info">
            来源：{{ job.source }}
          </el-tag>
          <el-tag
            v-if="job.job_url"
            size="default"
            effect="plain"
            type="warning"
            style="cursor: pointer"
            @click="window.open(job.job_url, '_blank')"
          >
            🔗 打开原链接
          </el-tag>
        </div>
      </div>

      <!-- 岗位解析（必备技能 / 加分技能 / 风险标签）-->
      <div v-if="job.analysis" class="sub-card">
        <div class="sub-head">📋 岗位解析</div>
        <div class="chips" v-if="job.analysis.required_skills?.length">
          <span class="chip-title">必备技能</span>
          <el-tag
            v-for="s in job.analysis.required_skills"
            :key="s"
            type="danger"
            effect="plain"
            style="margin: 3px"
          >
            {{ s }}
          </el-tag>
        </div>
        <div class="chips" v-if="job.analysis.preferred_skills?.length">
          <span class="chip-title">加分技能</span>
          <el-tag
            v-for="s in job.analysis.preferred_skills"
            :key="s"
            style="margin: 3px"
          >
            {{ s }}
          </el-tag>
        </div>
        <div class="chips" v-if="job.analysis.risk_tags?.length">
          <span class="chip-title">风险标签</span>
          <el-tag
            v-for="s in job.analysis.risk_tags"
            :key="s"
            type="warning"
            style="margin: 3px"
          >
            {{ s }}
          </el-tag>
        </div>
        <div v-if="!job.analysis.required_skills?.length && !job.analysis.preferred_skills?.length && !job.analysis.risk_tags?.length" class="empty-tip">
          （暂无解析结果，可点击下方「重新解析」按钮或去「开始分析」页跑一遍）
        </div>
      </div>

      <!-- JD 原文 -->
      <div class="sub-card">
        <div class="sub-head">📄 JD 原文</div>
        <pre class="jd">{{ job.jd_text }}</pre>
      </div>

      <!-- 底部操作：跳到推荐结果页 -->
      <div class="big-card-foot">
        <el-button type="primary" plain @click="gotoResults">
          查看分析结果（推荐结果页） →
        </el-button>
        <span class="foot-hint">分析结果（匹配评分 / 缺口 / 面试题 / BOSS 话术）请到「推荐结果」页查看</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* === 大卡片容器（整张图就是一张卡）=== */
.big-card {
  position: relative;
  background: #ffffff;
  border-radius: 16px;
  padding: 28px 32px 28px;
  box-shadow: 0 4px 24px rgba(20, 40, 90, 0.08);
  border: 1px solid #ebeef5;
}
.big-card-head {
  background: linear-gradient(135deg, #ffffff 0%, #f7f9ff 100%);
  border: 1px solid #e6ecff;
  border-radius: 12px;
  padding: 22px 24px;
  margin-bottom: 20px;
}
.hero-title {
  font-size: 24px;
  font-weight: 800;
  color: #1f2733;
  margin-bottom: 14px;
  letter-spacing: 0.5px;
}
.hero-title .dot {
  margin: 0 8px;
  color: #b8bfca;
  font-weight: 400;
}
.hero-tags {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

/* === 子卡（在大卡内分组）=== */
.sub-card {
  background: #fafbfd;
  border-radius: 10px;
  padding: 18px 20px;
  border: 1px solid #ebeef5;
  margin-bottom: 16px;
}
.sub-card:last-of-type {
  margin-bottom: 0;
}
.sub-head {
  font-size: 16px;
  font-weight: 700;
  color: #1f2733;
  margin-bottom: 10px;
}
.empty-tip {
  color: #8a94a6;
  font-size: 13px;
  font-style: italic;
  margin: 0;
}

/* === 岗位解析 chip === */
.chips {
  margin: 8px 0;
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 4px;
}
.chips:first-of-type {
  margin-top: 0;
}
.chip-title {
  color: #8a94a6;
  font-size: 14px;
  margin-right: 8px;
  min-width: 70px;
  line-height: 28px;
}

/* === JD 原文 === */
.jd {
  white-space: pre-wrap;
  background: #f5f7fb;
  padding: 20px 24px;
  border-radius: 8px;
  font-family: "Microsoft YaHei", "PingFang SC", "Heiti SC", "SimHei", "黑体", sans-serif;
  font-size: 16px;
  line-height: 1.85;
  color: #1f2733;
  font-weight: 500;
  border: 1px solid #ebeef5;
  margin: 0;
}

/* === 底部操作区 === */
.big-card-foot {
  margin-top: 18px;
  padding-top: 18px;
  border-top: 1px dashed #ebeef5;
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.foot-hint {
  color: #8a94a6;
  font-size: 13px;
}

/* === 大卡片右上角 X 按钮（仅 modal 模式显示）=== */
.bigcard-close {
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 10;
  width: 34px;
  height: 34px;
  border: none;
  background: rgba(255, 255, 255, 0.9);
  color: #1f2733;
  font-size: 22px;
  font-weight: 500;
  line-height: 1;
  border-radius: 50%;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
  transition: transform 0.15s, background 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}
.bigcard-close:hover {
  background: #f5f7fb;
  transform: scale(1.1);
}
</style>
