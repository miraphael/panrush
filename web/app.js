/* PanHub Web UI：无构建依赖，原生 JS。 */
"use strict";

const $ = (s) => document.querySelector(s);
let providers = [];
let current = null;          // 当前浏览的 provider 对象
let currentPath = null;      // 当前路径（fid 或 "/"）
const crumbs = [];           // [{label, path}]

const TIER_LABEL = { full: "满速", limited: "受限", experimental: "计划中" };

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

const fmtSize = (n) => {
  if (n < 0) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(i ? 1 : 0) + " " + u[i];
};
const fmtSpeed = (n) => (n ? fmtSize(n) + "/s" : "—");

/* ---------- 提供方侧栏 ---------- */

async function loadProviders() {
  providers = await api("/api/providers");
  const list = $("#provider-list");
  list.innerHTML = "";
  for (const p of providers) {
    const card = document.createElement("div");
    card.className = "pcard" + (current && current.id === p.id ? " active" : "");
    card.dataset.pid = p.id;
    card.innerHTML = `
      <div class="row"><span class="name">${p.name}</span>
        <span class="badge ${p.tier}">${TIER_LABEL[p.tier] || p.tier}</span></div>
      <div class="actions">
        ${p.cred_fields.length ? `<button class="btn-cred">${p.cred_saved() ? "重新配置" : "配置凭据"}</button>` : ""}
        ${p.id !== "direct" ? `<button class="btn-open">浏览</button>` : ""}
      </div>
      <div class="pcred">
        <div class="help">${p.help || ""}</div>
        ${p.cred_fields.map(f => `
          <input data-key="${f.key}" type="${f.secret ? "password" : "text"}"
                 placeholder="${f.label}${f.placeholder ? "：" + f.placeholder : ""}">`).join("")}
        <button class="primary btn-save-cred">保存并验证</button>
      </div>`;
    card.querySelector(".btn-cred")?.addEventListener("click", (e) => {
      e.stopPropagation();
      card.querySelector(".pcred").classList.toggle("open");
    });
    card.querySelector(".btn-save-cred")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      const values = {};
      card.querySelectorAll(".pcred input").forEach(i => values[i.dataset.key] = i.value.trim());
      try {
        await api(`/api/providers/${p.id}/cred`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ values }),
        });
        card.querySelector(".pcred").classList.remove("open");
        await loadProviders();
        alert(p.name + " 凭据验证通过 ✓");
      } catch (err) { alert("凭据验证失败：" + err.message); }
    });
    card.querySelector(".btn-open")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openProvider(p);
    });
    card.addEventListener("click", () => openProvider(p));
    list.appendChild(card);
  }
}

/* ---------- 文件浏览 ---------- */

async function openProvider(p) {
  current = p; currentPath = null; crumbs.length = 0;
  document.querySelectorAll(".pcard").forEach(c =>
    c.classList.toggle("active", c.dataset.pid === p.id));
  $("#cred-panel").classList.add("hidden");
  $("#welcome").classList.add("hidden");
  $("#file-panel").classList.remove("hidden");
  await browse(p.root);
}

async function browse(path) {
  if (!current) return;
  const data = await api(`/api/fs?provider=${current.id}&path=${encodeURIComponent(path)}`);
  currentPath = data.path;
  renderBreadcrumb();
  const tbody = $("#file-list");
  tbody.innerHTML = "";
  if (!data.entries.length) {
    tbody.innerHTML = `<tr><td colspan="3" class="empty">空目录</td></tr>`;
  }
  for (const e of data.entries) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="name ${e.is_dir ? "dir" : ""}">${e.is_dir ? "📁 " : "📄 "}${escapeHtml(e.name)}</td>
      <td class="size-col">${e.is_dir ? "" : fmtSize(e.size)}</td>
      <td class="act-col">${e.is_dir ? "" : `<button class="primary dl-btn">下载</button>`}</td>`;
    tr.querySelector("td.name").addEventListener("click", () => {
      if (e.is_dir) {
        crumbs.push({ label: e.name, path: e.fsid });
        browse(e.fsid);
      }
    });
    tr.querySelector(".dl-btn")?.addEventListener("click", async () => {
      try {
        await api("/api/tasks", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider: current.id, fsid: e.fsid, name: e.name, size: e.size,
            dest_dir: $("#cfg-dest").value.trim(),
          }),
        });
        pollTasks();
      } catch (err) { alert("创建任务失败：" + err.message); }
    });
    tbody.appendChild(tr);
  }
}

function renderBreadcrumb() {
  const crumb = $("#breadcrumb");
  const parts = [`<a data-i="-1">${current.name}</a>`];
  crumbs.forEach((c, i) => parts.push(` / <a data-i="${i}">${escapeHtml(c.label)}</a>`));
  crumb.innerHTML = parts.join("");
  crumb.querySelectorAll("a").forEach(a => a.addEventListener("click", () => {
    const i = parseInt(a.dataset.i);
    crumbs.length = i + 1;
    browse(i < 0 ? current.root : crumbs[i].path);
  }));
}

/* ---------- 任务 ---------- */

async function pollTasks() {
  let tasks;
  try { tasks = await api("/api/tasks"); } catch { return; }
  const total = tasks.filter(t => t.status === "downloading").reduce((s, t) => s + t.speed, 0);
  $("#total-speed").textContent = fmtSpeed(total);

  const box = $("#task-list");
  box.innerHTML = "";
  for (const t of tasks) {
    const div = document.createElement("div");
    div.className = `task ${t.status}`;
    const STATUS = { pending: "等待", resolving: "取直链", downloading: "下载中",
                     paused: "已暂停", done: "完成", error: "失败" };
    div.innerHTML = `
      <div class="trow"><span class="tname" title="${escapeHtml(t.name)}">${escapeHtml(t.name)}</span>
        <span class="tmeta">${STATUS[t.status] || t.status}</span></div>
      <div class="bar"><div style="width:${Math.min(100, t.percent)}%"></div></div>
      <div class="trow"><span class="tmeta">${fmtSize(t.done)} / ${t.size > 0 ? fmtSize(t.size) : "?"} · ${fmtSpeed(t.speed)}</span>
        <span class="tacts">
          ${["paused", "error"].includes(t.status) ? `<button data-act="resume">继续</button>` : ""}
          ${["downloading", "resolving", "pending"].includes(t.status) ? `<button data-act="pause">暂停</button>` : ""}
          ${t.status === "error" ? `<button data-act="retry">重试</button>` : ""}
          <button data-act="delete">删除</button>
        </span></div>
      ${t.error ? `<div class="terr">${escapeHtml(t.error)}</div>` : ""}`;
    div.querySelectorAll("[data-act]").forEach(b => b.addEventListener("click", async () => {
      await api(`/api/tasks/${t.id}/${b.dataset.act}`, { method: "POST" });
      pollTasks();
    }));
    box.appendChild(div);
  }
}

/* ---------- 直链快捷下载 ---------- */

async function quickDownload() {
  const url = $("#quick-url").value.trim();
  if (!url) return;
  try {
    await api("/api/download/url", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, dest_dir: $("#cfg-dest").value.trim() }),
    });
    $("#quick-url").value = "";
    pollTasks();
  } catch (err) { alert("失败：" + err.message); }
}

/* ---------- 设置 ---------- */

async function loadConfig() {
  const c = await api("/api/config");
  $("#cfg-dest").value = c.dest_dir;
  $("#cfg-conns").value = c.conns_per_task;
}

async function saveConfig() {
  await api("/api/config", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dest_dir: $("#cfg-dest").value.trim(),
      conns_per_task: parseInt($("#cfg-conns").value) || 16,
      global_speed_limit_kbps: parseInt($("#cfg-limit").value) || 0,
    }),
  });
  alert("设置已保存");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------- init ---------- */

$("#quick-btn").addEventListener("click", quickDownload);
$("#quick-url").addEventListener("keydown", (e) => { if (e.key === "Enter") quickDownload(); });
$("#cfg-save").addEventListener("click", saveConfig);
$("#cfg-open").addEventListener("click", async () => {
  const err = await api("/api/open-dir", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: $("#cfg-dest").value.trim() }),
  }).then(() => null, (e) => e);
  if (err) alert(err.message);
});
$("#pause-all").addEventListener("click", () => api("/api/tasks-all/pause", { method: "POST" }).then(pollTasks));
$("#resume-all").addEventListener("click", () => api("/api/tasks-all/resume", { method: "POST" }).then(pollTasks));

loadProviders().then(loadConfig);
pollTasks();
setInterval(pollTasks, 1000);
