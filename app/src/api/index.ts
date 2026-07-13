import axios from "axios";

const http = axios.create({ baseURL: "", timeout: 120000 });

export interface ProjectItem {
  name: string;
  description: string;
  keywords: string[];
}
export interface ResumeProfile {
  name: string;
  target_roles: string[];
  skills: string[];
  projects: ProjectItem[];
  strengths: string[];
  weaknesses: string[];
  graduation_year?: number | null;
  available_days_per_week?: number | null;
}
export interface Resume {
  id: number;
  filename: string;
  raw_text: string;
  profile_json: ResumeProfile | null;
}
export interface JobProfile {
  company_name: string;
  job_title: string;
  city: string;
  salary: string;
  education: string;
  experience: string;
  job_type: string;
  internship_days_per_week?: number | null;
  internship_duration?: string;
  graduation_years?: number[];
  required_skills: string[];
  preferred_skills: string[];
  responsibilities: string[];
  requirements: string[];
  risk_tags: string[];
  jd_summary?: string;
}
export interface Job {
  id: number;
  source: string;
  company_name: string;
  job_title: string;
  city: string;
  salary: string;
  education: string;
  experience: string;
  jd_text: string;
  jd_summary: string;
  job_url: string;
  analyze_mode: "summary" | "full";
  parse_status: string;
  parse_error: string;
  analysis: JobProfile | null;
}
export interface AgentRun {
  id: number;
  task_id: string;
  agent_name: string;
  step_order: number;
  status: string;
  summary: string;
  progress: number;
  output_json: any;
  error_message: string;
  eta_seconds: number;
  eta_low: number;
  eta_high: number;
  current_item: string;
  total_items: number;
  completed_items: number;
  failed_items: number;
  in_flight_items: { job_id: number; job_title: string }[] | null;
}
export interface WorkflowTask {
  task_id: string;
  status: string;
  steps: AgentRun[];
}
export interface MatchResult {
  id: number;
  resume_id: number;
  job_id: number;
  task_id: string | null;
  score: number;
  level: string;
  recommendation: string;
  matched_points: string[];
  missing_points: string[];
  risk_notes: string[];
  detail_json: any;
  cache_hit: boolean;
  report: any;
  company_name: string;
  job_title: string;
  city: string;
  salary: string;
  // P1#7 两档匹配：quick（快速档）/ deep（深度档）
  match_mode: string;
  // P2#14 单条状态：success / failed
  status: string;
  error_message: string;
  report_cache_key: string | null;
}
export interface PaginatedResults {
  items: MatchResult[];
  total: number;
  page: number;
  page_size: number;
}
export interface ReportItem {
  id: number;
  resume_id: number;
  task_id: string | null;
  title: string;
  summary: string;
  markdown_content: string;
}

export interface ResumeSummary {
  id: number;
  filename: string;
  profile_name: string;
  has_profile: boolean;
  created_at: string | null;
}

export interface BatchAnalyzeItem {
  id: number;
  ok: boolean;
  error: string;
}

export interface AgentHealth {
  name: string;
  role: string;
  model: string;
  provider: string;
  enable_thinking: boolean;
  configured: boolean;
}
export interface HealthInfo {
  status: string;
  llm_model: string;
  has_api_key: boolean;
  llm_base_url?: string;
  llm_timeout?: number;
  agents: AgentHealth[];
}

export const api = {
  health: () => http.get<HealthInfo>("/health").then((r) => r.data),
  testLLM: () => http.post("/api/test-llm").then((r) => r.data),

  parseResumeText: (text: string, filename = "pasted.txt") =>
    http.post<Resume>("/api/resumes/parse", { text, filename }).then((r) => r.data),
  uploadResume: (file: File, onProgress?: (percent: number) => void) => {
    const fd = new FormData();
    fd.append("file", file);
    return http
      .post<Resume>("/api/resumes/upload", fd, {
        onUploadProgress: (e) => {
          if (onProgress && e.total) {
            onProgress(Math.round((e.loaded * 100) / e.total));
          }
        },
      })
      .then((r) => r.data);
  },
  parseExistingResume: (id: number) =>
    http.post<Resume>(`/api/resumes/${id}/parse`).then((r) => r.data),
  getResume: (id: number) => http.get<Resume>(`/api/resumes/${id}`).then((r) => r.data),
  updateProfile: (id: number, profile: ResumeProfile) =>
    http.put<Resume>(`/api/resumes/${id}/profile`, { profile_json: profile }).then((r) => r.data),
  deleteResume: (id: number) =>
    http.delete<{ ok: boolean; id: number }>(`/api/resumes/${id}`).then((r) => r.data),
  listResumeSummary: () =>
    http.get<ResumeSummary[]>("/api/resumes/summary/list").then((r) => r.data),

  importJobsText: (jd_text: string, split_batch: boolean) =>
    http.post<Job[]>("/api/jobs/import-text", { jd_text, split_batch }).then((r) => r.data),
  importJobsFile: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return http.post<Job[]>("/api/jobs/import-file", fd).then((r) => r.data);
  },
  importJobUrl: (url: string) =>
    http.post<Job>("/api/jobs/import-url", { url }).then((r) => r.data),
  importJobImages: (files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return http
      .post<{ created: Job[]; failed: { filename: string; error: string }[] }>(
        "/api/jobs/import-images",
        fd
      )
      .then((r) => r.data);
  },
  listJobs: () => http.get<Job[]>("/api/jobs").then((r) => r.data),
  getJob: (id: number) => http.get<Job>(`/api/jobs/${id}`).then((r) => r.data),
  deleteJob: (id: number) =>
    http.delete<{ ok: boolean; id: number }>(`/api/jobs/${id}`).then((r) => r.data),
  batchDeleteJobs: (ids: number[]) =>
    http
      .delete<{ ok: boolean; deleted: number[] }>(`/api/jobs`, {
        params: { ids: ids.join(",") },
      })
      .then((r) => r.data),
  batchAnalyzeJobs: (ids: number[]) =>
    http
      .post<BatchAnalyzeItem[]>("/api/jobs/analyze-batch", { ids })
      .then((r) => r.data),
  setJobAnalyzeMode: (id: number, mode: "summary" | "full") =>
    http
      .put<Job>(`/api/jobs/${id}/analyze-mode`, { analyze_mode: mode })
      .then((r) => r.data),

  runAgents: (resume_id: number, job_ids: number[]) =>
    http.post<WorkflowTask>("/api/agents/run", { resume_id, job_ids }).then((r) => r.data),
  getTask: (task_id: string) =>
    http.get<WorkflowTask>(`/api/agents/tasks/${task_id}`).then((r) => r.data),
  abortTask: (task_id: string) =>
    http
      .post<{ ok: boolean; task_id: string; aborted: string[] }>(
        `/api/agents/tasks/${task_id}/abort`
      )
      .then((r) => r.data),

  listResults: (params?: { task_id?: string; page?: number; page_size?: number }) =>
    http
      .get<PaginatedResults>("/api/match/results", { params: params ?? {} })
      .then((r) => r.data),
  getResult: (id: number) =>
    http.get<MatchResult>(`/api/match/results/${id}`).then((r) => r.data),
  getResultByJob: (jobId: number) =>
    http
      .get<MatchResult | null>(`/api/match/results/by-job/${jobId}`)
      .then((r) => r.data),

  // P2#14 单/批量重试失败的匹配结果（不传 result_ids 则重试该 task 下所有 failed）
  retryMatchResults: (result_ids: number[] = [], task_id?: string) =>
    http
      .post<{ requested: number; generated: number; errors: any[] }>(
        "/api/match/results/retry",
        { result_ids },
        { params: task_id ? { task_id } : {} }
      )
      .then((r) => r.data),

  // P2#10 单条执行记录（AgentItemRun）
  listItemRuns: (task_id: string, agent_name?: string) =>
    http
      .get<any[]>("/api/match/item-runs", {
        params: agent_name ? { task_id, agent_name } : { task_id },
      })
      .then((r) => r.data),

  generateReports: (match_result_ids: number[], mode: "standard" | "deep" = "standard") =>
    http
      .post<
        | { mode: string; requested: number; generated: number; cache_hits: number; errors: any[] }
        | { task_id: string; status: string; total_items: number; mode: string }
      >("/api/reports/generate-batch", { match_result_ids, mode })
      .then((r) => r.data),

  // 深度报告后台任务进度轮询
  getReportTask: (taskId: string) =>
    http
      .get<{ task_id: string; status: string; total: number; done: number; failed: number }>(
        `/api/reports/tasks/${taskId}`
      )
      .then((r) => r.data),

  getReportTaskItems: (taskId: string) =>
    http
      .get<
        {
          item_id: number;
          item_label: string;
          status: string;
          error_message: string;
          duration_ms: number;
        }[]
      >(`/api/reports/tasks/${taskId}/items`)
      .then((r) => r.data),

  listReports: () => http.get<ReportItem[]>("/api/reports").then((r) => r.data),
  getReport: (id: number) => http.get<ReportItem>(`/api/reports/${id}`).then((r) => r.data),
  markdownUrl: (id: number) => `/api/reports/${id}/markdown`,
  excelUrl: (id: number) => `/api/reports/${id}/excel`,
};
