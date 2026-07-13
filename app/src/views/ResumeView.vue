<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
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
const uploadPhase = ref<"idle" | "uploading" | "ocr" | "parsing" | "done">("idle");

const pendingImageFiles = ref<File[]>([]);
const pendingImagePreviews = ref<string[]>([]);

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
    // 简历列表属于辅助视图，失败时不阻断主流程
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
      `确定删除「${label}」吗？依赖它的匹配结果也会一并清理。`,
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

function addPendingImage(file: File) {
  pendingImageFiles.value.push(file);
  pendingImagePreviews.value.push(URL.createObjectURL(file));
}

function removePendingImage(idx: number) {
  const url = pendingImagePreviews.value[idx];
  if (url) URL.revokeObjectURL(url);
  pendingImageFiles.value.splice(idx, 1);
  pendingImagePreviews.value.splice(idx, 1);
}

function clearPendingImages() {
  pendingImagePreviews.value.forEach((url) => URL.revokeObjectURL(url));
  pendingImageFiles.value = [];
  pendingImagePreviews.value = [];
}

function handleResumeImageChange(file: any) {
  if (!file?.raw) return;
  addPendingImage(file.raw as File);
}

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
    const parsed = await api.uploadResume(opt.file, (pct) => {
      uploadProgress.value = pct;
    });
    uploadPhase.value = "parsing";
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

async function confirmImportResumeImages() {
  if (!pendingImageFiles.value.length) {
    ElMessage.warning("请先选择简历图片");
    return;
  }
  loading.value = true;
  uploadPhase.value = "ocr";
  try {
    const files = [...pendingImageFiles.value];
    const result = await api.importResumeImages(files);
    uploadPhase.value = "parsing";
    applyResume(result.resume);
    uploadPhase.value = "done";
    clearPendingImages();

    if (result.failed.length === 0) {
      ElMessage.success(
        `已将 ${result.success} 张图片合并为 1 份简历，并完成识别${result.provider.includes("vision") ? "（含视觉模型兜底）" : ""}`
      );
    } else {
      const detail = result.failed
        .slice(0, 3)
        .map((item) => `${item.filename}: ${item.error}`)
        .join("；");
      ElMessage.warning(
        `已合并 ${result.success}/${result.total} 张图片为 1 份简历，${result.failed.length} 张失败${detail ? `（${detail}${result.failed.length > 3 ? "…" : ""}）` : ""}`
      );
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "简历图片识别失败");
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

onMounted(async () => {
  await refreshList();
  if (store.resumeId && store.profile) {
    resume.value = store.resume;
    profile.value = store.profile;
  } else if (store.resumeId) {
    try {
      const r = await api.getResume(store.resumeId);
      applyResume(r);
    } catch {
      // 旧缓存失效时静默放弃恢复
    }
  }
});

onBeforeUnmount(() => {
  clearPendingImages();
});
</script>

<template>
  <div class="page">
    <div class="page-title">简历画像</div>
    <div class="page-sub">
      支持多份简历长期并存管理；文本粘贴与文档上传按普通简历处理，图片 OCR
      单次只生成 1 份简历，但支持多张图片合成为同一份。
    </div>

    <div class="layout">
      <div class="card list-card">
        <div class="list-head">
          <b>我的简历（{{ resumeList.length }}）</b>
        </div>
        <el-empty
          v-if="resumeList.length === 0"
          description="还没有简历，请在右侧新增"
          :image-size="60"
        />
        <div
          v-for="r in resumeList"
          :key="r.id"
          :class="['resume-row', { on: activeId === r.id }]"
        >
          <div class="resume-main" @click="selectResume(r.id)">
            <div class="resume-name">{{ r.profile_name || r.filename }}</div>
            <div class="resume-sub">
              <el-tag v-if="r.has_profile" size="small" type="success">已画像</el-tag>
              <el-tag v-else size="small" type="info">未画像</el-tag>
              <span class="filename">· {{ r.filename }}</span>
            </div>
          </div>
          <el-button link type="danger" size="small" @click="deleteResume(r.id)" :disabled="loading">
            删除
          </el-button>
        </div>
      </div>

      <div class="editor">
        <div class="card">
          <div class="section-h">新增简历</div>
          <el-input
            v-model="text"
            type="textarea"
            :rows="8"
            placeholder="在此粘贴简历文本…"
          />

          <div class="action-row">
            <el-button type="primary" :loading="loading" @click="parseText">解析文本</el-button>
            <el-upload :show-file-list="false" :http-request="uploadFile" accept=".pdf,.docx,.md,.txt">
              <el-button :loading="loading">上传文件（PDF / DOCX / MD / TXT）</el-button>
            </el-upload>
            <el-upload
              :show-file-list="false"
              :auto-upload="false"
              :on-change="handleResumeImageChange"
              accept="image/png,image/jpeg,image/jpg,image/bmp,image/webp"
              multiple
            >
              <el-button :loading="loading">上传简历图片 OCR</el-button>
            </el-upload>
            <el-button
              v-if="pendingImageFiles.length > 0"
              type="success"
              :loading="loading"
              @click="confirmImportResumeImages"
            >
              合成并识别（{{ pendingImageFiles.length }} 张）
            </el-button>
            <el-button v-if="pendingImageFiles.length > 0" plain @click="clearPendingImages">
              清空图片
            </el-button>
          </div>

          <div class="upload-help">
            图片 OCR 单次只生成 1 份简历，适合多页截图 / 多页扫描件合并识别。
          </div>

          <div v-if="pendingImageFiles.length" class="image-preview-bar">
            <div class="preview-label">待合成图片（{{ pendingImageFiles.length }}）</div>
            <div class="preview-list">
              <div v-for="(url, idx) in pendingImagePreviews" :key="idx" class="preview-item">
                <img :src="url" :alt="pendingImageFiles[idx]?.name" />
                <button class="preview-remove" type="button" @click="removePendingImage(idx)">×</button>
                <div class="preview-name" :title="pendingImageFiles[idx]?.name">
                  {{ pendingImageFiles[idx]?.name }}
                </div>
              </div>
            </div>
          </div>

          <div v-if="uploadPhase !== 'idle'" class="progress-wrap">
            <el-progress
              :percentage="uploadPhase === 'uploading' ? uploadProgress : 100"
              :status="uploadPhase === 'done' ? 'success' : undefined"
              :indeterminate="uploadPhase === 'ocr' || uploadPhase === 'parsing'"
              :stroke-width="14"
            />
            <div class="progress-text">
              <template v-if="uploadPhase === 'uploading'">文件上传中… {{ uploadProgress }}%</template>
              <template v-else-if="uploadPhase === 'ocr'">图片上传完成，正在进行 OCR 识别…</template>
              <template v-else-if="uploadPhase === 'parsing'">文本已就绪，Resume Agent 正在解析简历…</template>
              <template v-else>处理完成</template>
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

          <div class="footer-actions">
            <el-button @click="saveProfile">保存画像</el-button>
            <el-button type="primary" @click="router.push('/jobs')">下一步：导入岗位</el-button>
          </div>
        </div>

        <el-empty
          v-else
          class="card"
          description="还没有选中的简历，请从左侧选择一份，或在上方新增"
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
  border-radius: 12px;
  cursor: pointer;
  margin-bottom: 8px;
  background: rgba(250, 251, 255, 0.85);
  border: 1px solid transparent;
}

.resume-row:hover {
  background: rgba(240, 244, 255, 0.95);
}

.resume-row.on {
  background: rgba(238, 243, 255, 0.98);
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

.action-row {
  margin-top: 12px;
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}

.upload-help {
  margin-top: 10px;
  font-size: 13px;
  color: #6a7486;
}

.image-preview-bar {
  margin-top: 14px;
  padding: 14px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.58);
  border: 1px solid rgba(150, 180, 255, 0.22);
}

.preview-label {
  margin-bottom: 10px;
  font-size: 13px;
  color: #5a6472;
}

.preview-list {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.preview-item {
  position: relative;
  width: 112px;
}

.preview-item img {
  width: 112px;
  height: 140px;
  object-fit: cover;
  border-radius: 12px;
  display: block;
  border: 1px solid rgba(150, 180, 255, 0.25);
}

.preview-remove {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 999px;
  background: rgba(12, 18, 32, 0.72);
  color: #fff;
  cursor: pointer;
}

.preview-name {
  margin-top: 6px;
  font-size: 12px;
  color: #6a7486;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.progress-wrap {
  margin-top: 16px;
}

.progress-text {
  margin-top: 6px;
  font-size: 13px;
  color: #5a6472;
}

.proj {
  border-left: 3px solid #3a6ff7;
  padding: 6px 12px;
  margin-bottom: 10px;
  background: rgba(247, 249, 255, 0.86);
  border-radius: 8px;
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

.footer-actions {
  margin-top: 18px;
  display: flex;
  gap: 12px;
}

@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }

  .two-col {
    grid-template-columns: 1fr;
  }
}
</style>
