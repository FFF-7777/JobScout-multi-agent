<script setup lang="ts">
import { useRoute } from "vue-router";
import { computed } from "vue";

const route = useRoute();
const nav = [
  { name: "home", path: "/", label: "首页" },
  { name: "resume", path: "/resume", label: "简历画像" },
  { name: "jobs", path: "/jobs", label: "岗位导入" },
  { name: "run", path: "/run", label: "执行流程" },
  { name: "results", path: "/results", label: "推荐结果" },
  { name: "reports", path: "/reports", label: "报告导出" },
];
const active = computed(() => route.name);
</script>

<template>
  <div class="layout">
    <header class="topbar">
      <div class="topbar-shell">
        <div class="brand">
          <span class="logo">JS</span>
          <div>
            <div class="brand-name">JobScout</div>
            <div class="brand-sub">AI 求职岗位筛选与匹配助手</div>
          </div>
        </div>
        <nav class="nav">
          <router-link
            v-for="n in nav"
            :key="n.name"
            :to="n.path"
            :class="['nav-item', { on: active === n.name }]"
          >
            {{ n.label }}
          </router-link>
        </nav>
      </div>
    </header>
    <main>
      <router-view v-slot="{ Component }">
        <Suspense>
          <component :is="Component" />
          <template #fallback>
            <RouteViewSkeleton />
          </template>
        </Suspense>
      </router-view>
    </main>
  </div>
</template>

<style scoped>
.layout {
  min-height: 100vh;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  padding: 16px 18px 0;
}

.topbar-shell {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  min-height: 72px;
  padding: 14px 18px;
  border-radius: 24px;
  border: 1px solid rgba(169, 183, 212, 0.28);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.84), rgba(255, 255, 255, 0.66));
  box-shadow: 0 16px 44px rgba(120, 138, 173, 0.12);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo {
  width: 44px;
  height: 44px;
  border-radius: 15px;
  background: linear-gradient(135deg, #8ebfff, #5f7cff 42%, #7cb9ff);
  color: #fff;
  font-weight: 800;
  display: grid;
  place-items: center;
  box-shadow: 0 10px 24px rgba(95, 124, 255, 0.28);
}

.brand-name {
  font-weight: 800;
  font-size: 18px;
  letter-spacing: -0.02em;
}

.brand-sub {
  font-size: 12px;
  color: #74819b;
}

.nav {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.nav-item {
  text-decoration: none;
  color: #55627e;
  padding: 10px 16px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 600;
  transition: background 0.18s ease, color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.74);
  color: #2f456d;
  transform: translateY(-1px);
}

.nav-item.on {
  background: linear-gradient(135deg, rgba(108, 147, 255, 0.22), rgba(255, 255, 255, 0.92));
  color: #4162ef;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.66), 0 8px 16px rgba(95, 124, 255, 0.12);
}

main {
  padding-top: 6px;
}

@media (max-width: 980px) {
  .topbar {
    padding: 12px 12px 0;
  }

  .topbar-shell {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
