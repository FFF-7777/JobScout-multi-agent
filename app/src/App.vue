<script setup lang="ts">
import { useRoute } from "vue-router";
import { computed } from "vue";

const route = useRoute();
const nav = [
  { name: "home", path: "/", label: "首页" },
  { name: "resume", path: "/resume", label: "简历画像" },
  { name: "jobs", path: "/jobs", label: "岗位导入" },
  { name: "run", path: "/run", label: "Agent 执行" },
  { name: "results", path: "/results", label: "推荐结果" },
  { name: "reports", path: "/reports", label: "报告导出" },
];
const active = computed(() => route.name);
</script>

<template>
  <div class="layout">
    <header class="topbar">
      <div class="brand">
        <span class="logo">JS</span>
        <div>
          <div class="brand-name">JobScout</div>
          <div class="brand-sub">AI 求职岗位筛选与匹配多智能体助手</div>
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
    </header>
    <main>
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 28px;
  height: 62px;
  background: #fff;
  box-shadow: 0 1px 8px rgba(20, 40, 90, 0.06);
  position: sticky;
  top: 0;
  z-index: 10;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}
.logo {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: linear-gradient(135deg, #3a6ff7, #6a5cff);
  color: #fff;
  font-weight: 800;
  display: grid;
  place-items: center;
}
.brand-name {
  font-weight: 800;
  font-size: 17px;
}
.brand-sub {
  font-size: 12px;
  color: #8a94a6;
}
.nav {
  display: flex;
  gap: 6px;
}
.nav-item {
  text-decoration: none;
  color: #5a6472;
  padding: 8px 14px;
  border-radius: 8px;
  font-size: 14px;
}
.nav-item.on {
  background: #eef3ff;
  color: #3a6ff7;
  font-weight: 600;
}
</style>
