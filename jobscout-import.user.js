// ==UserScript==
// @name         JobScout 一键导入 JD
// @namespace    https://github.com/jobscout
// @version      1.0.0
// @description  在 BOSS 直聘 / 猎聘 / 智联招聘 / 拉勾 / 前程无忧 的岗位详情页注入「导入 JobScout」按钮，把 JD 文本发送到本地 JobScout 后端（http://127.0.0.1:8020）。
// @author       JobScout
// @match        https://www.zhipin.com/*
// @match        https://www.liepin.com/*
// @match        https://www.zhaopin.com/*
// @match        https://www.lagou.com/*
// @match        https://www.51job.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_addStyle
// @grant        GM_notification
// @connect      127.0.0.1
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  // 本地 JobScout 后端 import-text 接口
  const API = "http://127.0.0.1:8020/api/jobs/import-text";

  // 各平台 JD 正文容器选择器（脚本会取文本最长者）
  const SELECTORS = [
    ".job-detail",          // BOSS 直聘
    ".job-intro",           // 猎聘
    ".job-detail-box",      // 智联招聘
    ".position-content",    // 拉勾
    ".job-content",         // 前程无忧
    "article",
    "main",
  ];

  function extractJD() {
    // 1) 优先用平台专属选择器
    let best = "";
    for (const sel of SELECTORS) {
      const nodes = document.querySelectorAll(sel);
      nodes.forEach((n) => {
        const t = (n.innerText || "").replace(/\s+/g, " ").trim();
        if (t.length > best.length) best = t;
      });
    }
    // 2) 兜底：从正文块里挑含「职责/要求/描述」等关键词的最长段落
    if (best.length < 80) {
      const blocks = Array.from(document.body.querySelectorAll("div, section, p"))
        .map((n) => (n.innerText || "").replace(/\s+/g, " ").trim())
        .filter(
          (t) =>
            t.length > 80 &&
            /职责|要求|描述|资格|技能|经验|responsibilit|qualif|experience/i.test(t)
        );
      if (blocks.length) {
        best = blocks.sort((a, b) => b.length - a.length)[0];
      }
    }
    return best;
  }

  function importToJobScout() {
    const text = extractJD();
    if (!text) {
      GM_notification({ text: "未能从本页提取到 JD 文本", title: "JobScout" });
      return;
    }
    GM_xmlhttpRequest({
      method: "POST",
      url: API,
      headers: { "Content-Type": "application/json" },
      data: JSON.stringify({ jd_text: text, split_batch: false }),
      onload: (r) => {
        try {
          const j = JSON.parse(r.responseText);
          if (Array.isArray(j) && j.length) {
            // 导入接口只存 jd_text，job_title 此时为空，用 JD 首行做兜底名
            const firstLine = text.split("\n").map((s) => s.trim()).find(Boolean) || "岗位";
            const name = (j[0] && j[0].job_title) || firstLine;
            GM_notification({
              text: `已导入「${name.slice(0, 30)}」等 ${j.length} 个岗位`,
              title: "JobScout 导入成功",
            });
          } else {
            GM_notification({ text: "后端返回异常：" + r.responseText, title: "JobScout" });
          }
        } catch (e) {
          GM_notification({ text: "解析后端返回失败：" + r.responseText, title: "JobScout" });
        }
      },
      onerror: () => {
        GM_notification({
          text: "连接本地 JobScout 失败，请确认后端已启动（8020 端口）",
          title: "JobScout",
        });
      },
    });
  }

  function mountButton() {
    if (document.getElementById("jobscout-import-btn")) return;
    const btn = document.createElement("button");
    btn.id = "jobscout-import-btn";
    btn.textContent = "⬇ 导入 JobScout";
    GM_addStyle(`
      #jobscout-import-btn {
        position: fixed; right: 20px; bottom: 80px; z-index: 99999;
        background: #3a6ff7; color: #fff; border: none; border-radius: 8px;
        padding: 10px 16px; font-size: 14px; cursor: pointer;
        box-shadow: 0 4px 12px rgba(58,111,247,.4);
      }
      #jobscout-import-btn:hover { background: #2f5fe0; }
    `);
    btn.onclick = importToJobScout;
    document.body.appendChild(btn);
  }

  // SPA 路由切换后重新挂载按钮
  const observer = new MutationObserver(() => mountButton());
  observer.observe(document.documentElement, { childList: true, subtree: true });
  mountButton();
})();
