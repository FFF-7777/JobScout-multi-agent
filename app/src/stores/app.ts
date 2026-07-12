import { defineStore } from "pinia";
import type { Resume, ResumeProfile } from "@/api";

const STORAGE_KEY = "jobscout-store";

type Persisted = {
  resumeId: number | null;
  resumeName: string;
  resume: Resume | null;
  profile: ResumeProfile | null;
  taskId: string | null;
};

function loadPersisted(): Partial<Persisted> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Partial<Persisted>) : {};
  } catch {
    return {};
  }
}

function save(p: Persisted) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(p));
  } catch {
    /* 忽略持久化失败（如隐私模式禁用 localStorage） */
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
    setTask(id: string) {
      this.taskId = id;
      this._persist();
    },
    _persist() {
      save({
        resumeId: this.resumeId,
        resumeName: this.resumeName,
        resume: this.resume,
        profile: this.profile,
        taskId: this.taskId,
      });
    },
  },
});
