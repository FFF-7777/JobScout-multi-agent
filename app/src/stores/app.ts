import { defineStore } from "pinia";
import type { Resume, ResumeProfile, ResumeSummary } from "@/api";

const STORAGE_KEY = "jobscout-store";
const TASK_SESSION_KEY = "jobscout-current-task-id";
const REPORT_TASK_SESSION_KEY = "jobscout-current-report-task";
const STORAGE_VERSION = 7;

type Persisted = {
  v: number;
  resumeId: number | null;
  resumeName: string;
  resume: Resume | null;
  profile: ResumeProfile | null;
  resumeList: ResumeSummary[];
  selectedJobIds: number[];
};

function loadPersisted(): Partial<Persisted> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Partial<Persisted>;
    if (parsed.v !== STORAGE_VERSION) {
      return {
        resumeId: typeof parsed.resumeId === "number" ? parsed.resumeId : null,
        resumeName: parsed.resumeName ?? "",
        resume: parsed.resume ?? null,
        profile: parsed.profile ?? null,
        resumeList: Array.isArray(parsed.resumeList) ? parsed.resumeList : [],
        selectedJobIds: Array.isArray(parsed.selectedJobIds) ? parsed.selectedJobIds : [],
      };
    }
    return parsed;
  } catch {
    return {};
  }
}

function save(p: Persisted) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(p));
  } catch {
    /* 忽略持久化失败 */
  }
}

export const useAppStore = defineStore("app", {
  state: () => {
    const p = loadPersisted();
    return {
      resumeId: p.resumeId ?? null,
      resumeName: p.resumeName ?? "",
      resume: p.resume ?? null,
      profile: p.profile ?? null,
      // 当前分析任务只属于当前浏览器会话，不能从长期缓存恢复历史任务。
      taskId: sessionStorage.getItem(TASK_SESSION_KEY),
      resumeList: p.resumeList ?? [],
      selectedJobIds: p.selectedJobIds ?? [],
      reportTask: (() => {
        try {
          const value = sessionStorage.getItem(REPORT_TASK_SESSION_KEY);
          return value ? JSON.parse(value) : null;
        } catch {
          return null;
        }
      })(),
    };
  },
  actions: {
    setResume(r: Resume, p?: ResumeProfile) {
      this.resumeId = r.id;
      this.resumeName = r.profile_json?.name || r.filename;
      this.resume = r;
      this.profile = p ?? r.profile_json ?? null;
      this._persist();
    },
    clearResume() {
      this.resumeId = null;
      this.resumeName = "";
      this.resume = null;
      this.profile = null;
      this._persist();
    },
    setTask(id: string) {
      this.taskId = id;
      if (id) sessionStorage.setItem(TASK_SESSION_KEY, id);
      else sessionStorage.removeItem(TASK_SESSION_KEY);
    },
    setResumeList(list: ResumeSummary[]) {
      this.resumeList = list;
      this._persist();
    },
    removeResumeFromList(id: number) {
      this.resumeList = this.resumeList.filter((r) => r.id !== id);
      if (this.resumeId === id) {
        this.clearResume();
      }
      this._persist();
    },
    setSelectedJobIds(ids: number[]) {
      this.selectedJobIds = ids;
      this._persist();
    },
    setReportTask(task: { taskId: string; mode: "standard" | "deep"; total: number } | null) {
      this.reportTask = task;
      if (task) sessionStorage.setItem(REPORT_TASK_SESSION_KEY, JSON.stringify(task));
      else sessionStorage.removeItem(REPORT_TASK_SESSION_KEY);
    },
    _persist() {
      save({
        v: STORAGE_VERSION,
        resumeId: this.resumeId,
        resumeName: this.resumeName,
        resume: this.resume,
        profile: this.profile,
        resumeList: this.resumeList,
        selectedJobIds: this.selectedJobIds,
      });
    },
  },
});
