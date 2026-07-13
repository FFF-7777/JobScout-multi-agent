import { defineAsyncComponent } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import RouteViewSkeleton from "@/components/RouteViewSkeleton.vue";

function lazyView(loader: () => Promise<any>, delay = 120) {
  return defineAsyncComponent({
    loader,
    delay,
    loadingComponent: RouteViewSkeleton,
    suspensible: true,
  });
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: lazyView(() => import("@/views/HomeView.vue"), 80) },
    { path: "/resume", name: "resume", component: lazyView(() => import("@/views/ResumeView.vue"), 80) },
    { path: "/jobs", name: "jobs", component: lazyView(() => import("@/views/JobsView.vue"), 100) },
    { path: "/run", name: "run", component: lazyView(() => import("@/views/RunView.vue"), 100) },
    { path: "/results", name: "results", component: lazyView(() => import("@/views/ResultsView.vue"), 120) },
    { path: "/jobs/:id", name: "jobDetail", component: lazyView(() => import("@/views/JobDetailView.vue"), 120) },
    { path: "/reports", name: "reports", component: lazyView(() => import("@/views/ReportView.vue"), 120) },
  ],
});

export default router;
