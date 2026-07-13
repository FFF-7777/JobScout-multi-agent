import { defineStore } from "pinia";
import type { Resume, ResumeProfile, ResumeSummary } from "@/api";

const STORAGE_KEY = "jobscout-store";
const STORAGE_VERSION = 4;

type Persisted = {
  v: number;
  resumeId: number | null;
  resumeName: string;
  resume: Resume | null;
  profile: ResumeProfile | null;
  taskId: string | null;
  resumeList: ResumeSummary[];
  selectedJobIds: number[];
  reportTask: { taskId: string; mode: "standard" | "deep"; total: number } | null;
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
        taskId: parsed.taskId ?? null,
        resumeList: Array.isArray(parsed.resumeList) ? parsed.resumeList : [],
        selectedJobIds: Array.isArray(parsed.selectedJobIds) ? parsed.selectedJobIds : [],
        reportTask: parsed.reportTask ?? null,
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
      taskId: p.taskId ?? null,
      resumeList: p.resumeList ?? [],
      selectedJobIds: p.selectedJobIds ?? [],
      reportTask: p.reportTask ?? null,
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
      this._persist();
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
      this._persist();
    },
    _persist() {
      save({
        v: STORAGE_VERSION,
        resumeId: this.resumeId,
        resumeName: this.resumeName,
        resume: this.resume,
        profile: this.profile,
        taskId: this.taskId,
        resumeList: this.resumeList,
        selectedJobIds: this.selectedJobIds,
        reportTask: this.reportTask,
      });
    },
  },
});
