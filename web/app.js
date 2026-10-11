/* PanHub Web UI：无构建依赖，原生 JS。 */
"use strict";

const $ = (s) => document.querySelector(s);
let providers = [];
let current = null;          // 当前浏览的 provider 对象
let currentPath = null;      // 当前路径（fid 或 "/"）
const crumbs = [];           // [{label, path}]

const TIER_LABEL = { full: "满速", limited: "受限", experimental: "计划中" };

let taskFilter = "all";
const prevStatus = {};   // 任务完成通知用：id -> 上次状态
let speedHist = [];      // 总速度历史（秒），画曲线用

// 浏览器通知需要用户手势授权一次
document.addEventListener("click", () => {
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }
}, { once: true });

function notifyDone(t) {
  if ("Notification" in window && Notification.permission === "granted") {
    try {
      new Notification("PanRush · 下载完成 ✓", { body: t.name });
    } catch {}
  }
}

function drawSpeedChart() {
  const cv = $("#speed-chart"), ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (speedHist.length < 2) return;
  const max = Math.max(...speedHist, 1);
  ctx.beginPath();
  ctx.strokeStyle = "#34c77b";
  ctx.lineWidth = 1.5;
  speedHist.forEach((v, i) => {
    const x = i * cv.width / (speedHist.length - 1);
    const y = cv.height - 2 - (v / max) * (cv.height - 4);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.stroke();
}

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
const fmtDur = (s) => {
  if (!isFinite(s) || s <= 0) return "—";
  if (s < 60) return Math.ceil(s) + "s";
  if (s < 3600) return Math.floor(s / 60) + "m" + Math.ceil(s % 60) + "s";
  return Math.floor(s / 3600) + "h" + Math.floor((s % 3600) / 60) + "m";
};

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
  $("#gsearch").value = "";
  await browse(p.root);
}

let selected = new Set();   // 批量下载：当前目录勾选的文件 fsid

async function browse(path) {
  if (!current) return;
  const data = await api(`/api/fs?provider=${current.id}&path=${encodeURIComponent(path)}`);
  currentPath = data.path;
  selected = new Set();
  updateBatchBtn();
  renderBreadcrumb();
  const tbody = $("#file-list");
  tbody.innerHTML = "";
  if (!data.entries.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty">空目录</td></tr>`;
  }
  for (const e of data.entries) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="sel-col">${e.is_dir ? "" : `<input type="checkbox" class="row-sel" data-fsid="${escapeHtml(e.fsid)}">`}</td>
      <td class="name ${e.is_dir ? "dir" : ""}">${e.is_dir ? "📁 " : "📄 "}${escapeHtml(e.name)}</td>
      <td class="size-col">${e.is_dir ? "" : fmtSize(e.size)}</td>
      <td class="act-col">${e.is_dir ? "" : `<button class="primary dl-btn">下载</button>`}</td>`;
    tr.querySelector("td.name").addEventListener("click", () => {
      if (e.is_dir) {
        crumbs.push({ label: e.name, path: e.fsid });
        browse(e.fsid);
      }
    });
    tr.querySelector(".row-sel")?.addEventListener("change", (ev) => {
      ev.target.checked ? selected.add(e.fsid) : selected.delete(e.fsid);
      updateBatchBtn();
    });
    tr.querySelector(".dl-btn")?.addEventListener("click", () => createTask(current, e));
    tbody.appendChild(tr);
  }
}

async function createTask(provider, e) {
  try {
    await api("/api/tasks", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: provider.id, fsid: e.fsid, name: e.name, size: e.size,
        dest_dir: $("#cfg-dest").value.trim(),
      }),
    });
    pollTasks();
  } catch (err) { alert("创建任务失败：" + err.message); }
}

function updateBatchBtn() {
  const btn = $("#batch-dl");
  btn.classList.toggle("hidden", selected.size === 0);
  btn.textContent = selected.size ? `下载所选 (${selected.size})` : "下载所选";
}

async function batchDownload() {
  // 从当前目录行收集勾选文件的信息
  const rows = [...document.querySelectorAll("#file-list .row-sel")].filter(c => c.checked);
  for (const box of rows) {
    const e = box.dataset;
    const tr = box.closest("tr");
    const name = tr.querySelector("td.name").textContent.trim().replace(/^[📁📄]\s*/, "");
    const sizeText = tr.querySelector(".size-col").textContent.trim();
    await createTask(current, { fsid: e.fsid, name, size: -1 });
  }
}

/* ---------- 跨盘搜索 ---------- */

async function globalSearch() {
  const q = $("#gsearch").value.trim();
  if (!q) return;
  $("#welcome").classList.add("hidden");
  $("#cred-panel").classList.add("hidden");
  $("#file-panel").classList.remove("hidden");
  document.querySelectorAll(".pcard").forEach(c => c.classList.remove("active"));
  const tbody = $("#file-list");
  $("#batch-dl").classList.add("hidden");
  $("#breadcrumb").innerHTML = `🔍 搜索 “${escapeHtml(q)}” — <a id="search-back">返回浏览</a>`;
  tbody.innerHTML = `<tr><td colspan="4" class="empty">正在搜索所有已配置的网盘…</td></tr>`;
  $("#search-back").addEventListener("click", () => { if (current) openProvider(current); });
  let res;
  try { res = await api(`/api/search?q=${encodeURIComponent(q)}`); }
  catch (err) { tbody.innerHTML = `<tr><td colspan="4" class="empty">搜索失败：${escapeHtml(err.message)}</td></tr>`; return; }
  tbody.innerHTML = "";
  if (!res.results.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty">所有网盘都没有找到匹配文件（仅扫描已配置凭据的盘）</td></tr>`;
    return;
  }
  for (const r of res.results) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="sel-col"></td>
      <td class="name">📄 ${escapeHtml(r.name)}<span class="dim"> · ${escapeHtml(r.provider_name)} · ${escapeHtml(r.path)}</span></td>
      <td class="size-col">${fmtSize(r.size)}</td>
      <td class="act-col"><button class="primary dl-btn">下载</button></td>`;
    tr.querySelector(".dl-btn").addEventListener("click", () =>
      createTask({ id: r.provider }, { fsid: r.fsid, name: r.name, size: r.size }));
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
  $("#total-speed-text").textContent = fmtSpeed(total);
  speedHist.push(total);
  if (speedHist.length > 60) speedHist.shift();
  drawSpeedChart();

  // 自动关机倒计时提示
  api("/api/status").then(s => {
    const tip = $("#shutdown-tip");
    if (s.shutdown_at && s.auto_shutdown !== "none") {
      const left = Math.max(0, Math.ceil((s.shutdown_at * 1000 - Date.now()) / 1000));
      tip.textContent = `⏻ ${s.auto_shutdown === "shutdown" ? "关机" : "睡眠"}倒计时 ${left}s`;
      tip.classList.remove("hidden");
    } else {
      tip.classList.add("hidden");
    }
  }).catch(() => {});

  // 完成通知
  for (const t of tasks) {
    if (t.status === "done" && prevStatus[t.id] && prevStatus[t.id] !== "done") notifyDone(t);
    prevStatus[t.id] = t.status;
  }

  const filtered = tasks.filter(t => {
    if (taskFilter === "active") return ["pending", "resolving", "downloading", "paused"].includes(t.status);
    if (taskFilter === "done") return t.status === "done";
    if (taskFilter === "error") return t.status === "error";
    return true;
  });

  const box = $("#task-list");
  box.innerHTML = "";
  if (!filtered.length) {
    box.innerHTML = `<div class="empty">没有${{all:"",active:"进行中的",done:"已完成的",error:"失败的"}[taskFilter] || ""}任务</div>`;
  }
  for (const t of filtered) {
    const div = document.createElement("div");
    div.className = `task ${t.status}`;
    const STATUS = { pending: "等待", resolving: "取直链", downloading: "下载中",
                     paused: "已暂停", done: "完成", error: "失败" };
    div.innerHTML = `
      <div class="trow"><span class="tname" title="${escapeHtml(t.name)}">${escapeHtml(t.name)}</span>
        <span class="tmeta">${STATUS[t.status] || t.status}</span></div>
      <div class="bar"><div style="width:${Math.min(100, t.percent)}%"></div></div>
      <div class="trow"><span class="tmeta">${fmtSize(t.done)} / ${t.size > 0 ? fmtSize(t.size) : "?"} · ${fmtSpeed(t.speed)}${t.status === "downloading" && t.speed > 0 && t.size > 0 ? ` · 剩余 ${fmtDur((t.size - t.done) / t.speed)}` : ""}</span>
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
  $("#cfg-tasks").value = c.max_concurrent_tasks;
  $("#cfg-limit").value = c.global_speed_limit_kbps || 0;
  $("#cfg-shutdown").value = c.auto_shutdown || "none";
  api("/api/autostart").then(s => { $("#cfg-autostart").checked = s.enabled; }).catch(() => {});
  api("/api/version").then(v => {
    $("#ver").textContent = `v${v.version}`;
  }).catch(() => {});
}

async function saveConfig() {
  await api("/api/config", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dest_dir: $("#cfg-dest").value.trim(),
      conns_per_task: parseInt($("#cfg-conns").value) || 16,
      max_concurrent_tasks: parseInt($("#cfg-tasks").value) || 3,
      global_speed_limit_kbps: parseInt($("#cfg-limit").value) || 0,
      auto_shutdown: $("#cfg-shutdown").value,
    }),
  });
  try {
    await api("/api/autostart", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enable: $("#cfg-autostart").checked }),
    });
  } catch (err) { alert("开机自启设置失败：" + err.message); }
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
$("#clear-done").addEventListener("click", () => api("/api/tasks-all/clear-done", { method: "POST" }).then(pollTasks));

$("#sel-all").addEventListener("change", (e) => {
  document.querySelectorAll("#file-list .row-sel").forEach(c => {
    c.checked = e.target.checked;
    const fsid = c.dataset.fsid;
    e.target.checked ? selected.add(fsid) : selected.delete(fsid);
  });
  updateBatchBtn();
});
$("#batch-dl").addEventListener("click", batchDownload);
$("#gsearch").addEventListener("keydown", (e) => { if (e.key === "Enter") globalSearch(); });

$("#ver").style.cursor = "pointer";
$("#ver").title = "点击检查更新";
$("#ver").addEventListener("click", async () => {
  try {
    const r = await api("/api/update");
    if (r.update_available) {
      if (confirm(`发现新版本 v${r.latest}（当前 v${r.current}），打开下载页？`)) window.open(r.url);
    } else {
      alert(`已是最新版本 v${r.current}` + (r.latest ? `（最新 v${r.latest}）` : "（仓库还没有发布版本）"));
    }
  } catch (err) { alert("检查更新失败：" + err.message); }
});

document.querySelectorAll(".ftab").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".ftab").forEach(x => x.classList.remove("active"));
  b.classList.add("active");
  taskFilter = b.dataset.f;
  pollTasks();
}));

loadProviders().then(loadConfig);
pollTasks();
setInterval(pollTasks, 1000);
