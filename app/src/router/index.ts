import { createRouter, createWebHistory } from "vue-router";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: () => import("@/views/HomeView.vue") },
    { path: "/resume", name: "resume", component: () => import("@/views/ResumeView.vue") },
    { path: "/jobs", name: "jobs", component: () => import("@/views/JobsView.vue") },
    { path: "/run", name: "run", component: () => import("@/views/RunView.vue") },
    { path: "/results", name: "results", component: () => import("@/views/ResultsView.vue") },
    { path: "/jobs/:id", name: "jobDetail", component: () => import("@/views/JobDetailView.vue") },
    { path: "/reports", name: "reports", component: () => import("@/views/ReportView.vue") },
  ],
});

export default router;
