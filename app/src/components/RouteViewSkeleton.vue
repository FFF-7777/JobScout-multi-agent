<template>
  <div class="page route-skeleton">
    <div class="eyebrow">Loading</div>
    <div class="route-skeleton-head">
      <div class="route-skeleton-title"></div>
      <div class="route-skeleton-sub"></div>
    </div>

    <div class="card route-skeleton-panel">
      <div class="route-skeleton-toolbar">
        <div class="route-skeleton-chip" v-for="n in 4" :key="n"></div>
      </div>
      <div class="route-skeleton-grid">
        <div class="route-skeleton-block large"></div>
        <div class="route-skeleton-block"></div>
      </div>
      <div class="route-skeleton-list">
        <div class="route-skeleton-row" v-for="n in 5" :key="n"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.route-skeleton {
  padding-top: 24px;
}

.route-skeleton-head {
  margin: 18px 0 22px;
}

.route-skeleton-title,
.route-skeleton-sub,
.route-skeleton-chip,
.route-skeleton-block,
.route-skeleton-row {
  position: relative;
  overflow: hidden;
  border-radius: 18px;
  background: linear-gradient(90deg, rgba(235, 240, 247, 0.96), rgba(247, 249, 252, 0.98), rgba(235, 240, 247, 0.96));
}

.route-skeleton-title::after,
.route-skeleton-sub::after,
.route-skeleton-chip::after,
.route-skeleton-block::after,
.route-skeleton-row::after {
  content: "";
  position: absolute;
  inset: 0;
  transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.72), transparent);
  animation: route-skeleton-shimmer 1.6s infinite;
}

.route-skeleton-title {
  width: min(360px, 72%);
  height: 38px;
}

.route-skeleton-sub {
  margin-top: 12px;
  width: min(620px, 92%);
  height: 16px;
  border-radius: 999px;
}

.route-skeleton-panel {
  padding: 24px;
}

.route-skeleton-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.route-skeleton-chip {
  width: 110px;
  height: 40px;
  border-radius: 999px;
}

.route-skeleton-grid {
  display: grid;
  grid-template-columns: 1.3fr 0.7fr;
  gap: 16px;
  margin-top: 18px;
}

.route-skeleton-block {
  height: 180px;
}

.route-skeleton-block.large {
  height: 220px;
}

.route-skeleton-list {
  margin-top: 18px;
}

.route-skeleton-row {
  height: 64px;
  margin-bottom: 12px;
}

@keyframes route-skeleton-shimmer {
  100% {
    transform: translateX(100%);
  }
}

@media (max-width: 900px) {
  .route-skeleton-grid {
    grid-template-columns: 1fr;
  }
}
</style>
