<script setup lang="ts">
import { ref, onMounted, computed } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import type { UploadRequestOptions } from "element-plus";
import { api, type Resume, type ResumeProfile, type ResumeSummary } from "@/api";
import { useAppStore } from "@/stores/app";

const router = useRouter();
const store = useAppStore();

const text = ref("");
const loading = ref(false);
const resume = ref<Resume | null>(null);
const profile = ref<ResumeProfile | null>(null);
const resumeList = ref<ResumeSummary[]>([]);

const uploadProgress = ref(0);
const uploadPhase = ref<"idle" | "uploading" | "parsing" | "done">("idle");

function applyResume(r: Resume) {
  resume.value = r;
  profile.value = r.profile_json;
  store.setResume(r, r.profile_json ?? undefined);
  refreshList();
}

async function refreshList() {
  try {
    const list = await api.listResumeSummary();
    resumeList.value = list;
    store.setResumeList(list);
  } catch {
    /* 静默：列表是辅助显示，主流程不依赖它 */
  }
}

async function selectResume(id: number) {
  if (id === store.resumeId) return;
  loading.value = true;
  try {
    const r = await api.getResume(id);
    applyResume(r);
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "切换简历失败");
  } finally {
    loading.value = false;
  }
}

async function deleteResume(id: number) {
  const target = resumeList.value.find((r) => r.id === id);
  const label = target?.profile_name || target?.filename || `简历 ${id}`;
  try {
    await ElMessageBox.confirm(
      `确定删除「${label}」吗？依赖它的匹配结果会一并清理。`,
      "删除简历",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" }
    );
  } catch {
    return;
  }
  loading.value = true;
  try {
    await api.deleteResume(id);
    ElMessage.success("已删除");
    if (store.resumeId === id) {
      resume.value = null;
      profile.value = null;
      store.clearResume();
    }
    await refreshList();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "删除失败");
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  await refreshList();
  if (store.resumeId && store.profile) {
    resume.value = store.resume;
    profile.value = store.profile;
  } else if (store.resumeId) {
    try {
      const r = await api.getResume(store.resumeId);
      applyResume(r);
    } catch (e) {
      // 简历已被删除或后端不可用时，静默放弃恢复
    }
  }
});

async function parseText() {
  if (!text.value.trim()) {
    ElMessage.warning("请粘贴简历文本");
    return;
  }
  loading.value = true;
  try {
    applyResume(await api.parseResumeText(text.value));
    ElMessage.success("简历画像解析完成");
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "解析失败，请检查后端与 API Key");
  } finally {
    loading.value = false;
  }
}

async function uploadFile(opt: UploadRequestOptions) {
  uploadPhase.value = "uploading";
  uploadProgress.value = 0;
  loading.value = true;
  try {
    const r = await api.uploadResume(opt.file, (pct) => {
      uploadProgress.value = pct;
    });
    uploadPhase.value = "parsing";
    const parsed = await api.parseExistingResume(r.id);
    applyResume(parsed);
    uploadPhase.value = "done";
    ElMessage.success("上传并解析完成");
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "上传失败");
  } finally {
    loading.value = false;
    setTimeout(() => {
      uploadProgress.value = 0;
      uploadPhase.value = "idle";
    }, 1500);
  }
}

async function saveProfile() {
  if (!resume.value || !profile.value) return;
  try {
    applyResume(await api.updateProfile(resume.value.id, profile.value));
    ElMessage.success("画像已保存");
  } catch {
    ElMessage.error("保存失败");
  }
}

const activeId = computed(() => store.resumeId);
</script>

<template>
  <div class="page">
    <div class="page-title">简历画像</div>
    <div class="page-sub">支持多份简历并存：上传、解析、切换、删除；Resume Agent 会提取技能、项目、目标岗位与优劣势。</div>

    <div class="layout">
      <!-- 左侧：我的简历列表 -->
      <div class="card list-card">
        <div class="list-head">
          <b>我的简历（{{ resumeList.length }}）</b>
        </div>
        <el-empty
          v-if="resumeList.length === 0"
          description="还没有简历，请在右侧上传或粘贴"
          :image-size="60"
        />
        <div
          v-for="r in resumeList"
          :key="r.id"
          :class="['resume-row', { on: activeId === r.id }]"
        >
          <div class="resume-main" @click="selectResume(r.id)">
            <div class="resume-name">
              {{ r.profile_name || r.filename }}
            </div>
            <div class="resume-sub">
              <el-tag v-if="r.has_profile" size="small" type="success">已画像</el-tag>
              <el-tag v-else size="small" type="info">未画像</el-tag>
              <span class="filename">· {{ r.filename }}</span>
            </div>
          </div>
          <el-button
            link
            type="danger"
            size="small"
            @click="deleteResume(r.id)"
            :disabled="loading"
          >
            删除
          </el-button>
        </div>
      </div>

      <!-- 右侧：编辑 / 上传当前简历 -->
      <div class="editor">
        <div class="card">
          <div class="section-h">新增简历</div>
          <el-input
            v-model="text"
            type="textarea"
            :rows="8"
            placeholder="在此粘贴简历文本…"
          />
          <div style="margin-top: 12px; display: flex; gap: 12px; align-items: center">
            <el-button type="primary" :loading="loading" @click="parseText">解析文本</el-button>
            <el-upload :show-file-list="false" :http-request="uploadFile" accept=".pdf,.docx,.md,.txt">
              <el-button :loading="loading">上传文件（PDF / DOCX / MD / TXT）</el-button>
            </el-upload>
          </div>

          <div v-if="uploadPhase !== 'idle'" style="margin-top: 16px">
            <el-progress
              :percentage="uploadPhase === 'uploading' ? uploadProgress : 100"
              :status="uploadPhase === 'done' ? 'success' : undefined"
              :indeterminate="uploadPhase === 'parsing'"
              :stroke-width="14"
            />
            <div style="margin-top: 6px; font-size: 13px; color: #5a6472">
              <template v-if="uploadPhase === 'uploading'">上传中… {{ uploadProgress }}%</template>
              <template v-if="uploadPhase === 'parsing'">上传完成，Resume Agent 正在解析简历…</template>
              <template v-if="uploadPhase === 'done'">解析完成</template>
            </div>
          </div>
        </div>

        <div v-if="profile && resume" class="card">
          <div class="section-h">候选人姓名</div>
          <el-input v-model="profile.name" style="max-width: 320px" />

          <div class="section-h">目标岗位</div>
          <el-select v-model="profile.target_roles" multiple filterable allow-create style="width: 100%">
            <el-option v-for="r in profile.target_roles" :key="r" :label="r" :value="r" />
          </el-select>

          <div class="section-h">技能标签</div>
          <el-select v-model="profile.skills" multiple filterable allow-create style="width: 100%">
            <el-option v-for="s in profile.skills" :key="s" :label="s" :value="s" />
          </el-select>

          <div class="section-h">项目经历</div>
          <div v-for="(p, i) in profile.projects" :key="i" class="proj">
            <b>{{ p.name }}</b>
            <div class="proj-desc">{{ p.description }}</div>
            <div>
              <el-tag v-for="k in p.keywords" :key="k" size="small" style="margin: 2px">{{ k }}</el-tag>
            </div>
          </div>

          <div class="two-col">
            <div>
              <div class="section-h">优势</div>
              <el-tag v-for="s in profile.strengths" :key="s" type="success" style="margin: 3px">{{ s }}</el-tag>
            </div>
            <div>
              <div class="section-h">短板</div>
              <el-tag v-for="w in profile.weaknesses" :key="w" type="warning" style="margin: 3px">{{ w }}</el-tag>
            </div>
          </div>

          <div style="margin-top: 18px; display: flex; gap: 12px">
            <el-button @click="saveProfile">保存画像</el-button>
            <el-button type="primary" @click="router.push('/jobs')">下一步：导入岗位</el-button>
          </div>
        </div>

        <el-empty
          v-else
          class="card"
          description="还没有选中的简历，请从左侧选一份或在上方新增"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 18px;
  align-items: start;
}
.list-card {
  padding: 14px;
}
.list-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.resume-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 6px;
  background: #fafbff;
  border: 1px solid transparent;
}
.resume-row:hover {
  background: #f0f4ff;
}
.resume-row.on {
  background: #eef3ff;
  border-color: #3a6ff7;
}
.resume-main {
  flex: 1;
  min-width: 0;
}
.resume-name {
  font-weight: 600;
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.resume-sub {
  margin-top: 4px;
  font-size: 12px;
  color: #5a6472;
  display: flex;
  align-items: center;
  gap: 6px;
}
.filename {
  color: #8a94a6;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.proj {
  border-left: 3px solid #3a6ff7;
  padding: 4px 12px;
  margin-bottom: 10px;
  background: #f7f9ff;
  border-radius: 6px;
}
.proj-desc {
  color: #5a6472;
  font-size: 13px;
  margin: 4px 0;
}
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-top: 8px;
}
@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
</style>
