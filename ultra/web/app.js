// ULTRA AI GOD MODE dashboard (no build step, works offline).
(() => {
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("ultra_" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ultra_" + k, JSON.stringify(v)); } catch {} },
};
let lang = store.get("lang", "ar");
let theme = store.get("theme", "dark");
const T = (k) => (I18N[lang] && I18N[lang][k]) || I18N.ar[k] || k;
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString(lang === "ar" ? "ar-DZ" : "en-GB", { dateStyle: "short", timeStyle: "short" }) : "—";
const badge = (st) => `<span class="badge b-${esc(st)}">${esc(T("st_" + st))}</span>`;

// Token for the Android/Windows apps (they cannot always keep cookies); browsers use the cookie.
const authToken = new URLSearchParams(location.hash.split("?")[1] || "").get("token") || store.get("token", "");

async function api(path, opts = {}) {
  const headers = opts.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  if (authToken) headers.Authorization = "Bearer " + authToken;
  const res = await fetch("/api" + path, { method: opts.method || "GET", headers, credentials: "same-origin",
    body: opts.body instanceof FormData ? opts.body : opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  let data = null;
  try { data = await res.json(); } catch {}
  if (res.status === 401 && !path.startsWith("/auth")) { showAuth(); throw new Error("login_required"); }
  if (!res.ok) { const e = new Error((data && data.error) || res.statusText); e.status = res.status; throw e; }
  return data;
}

function toast(msg, bad = false) {
  const el = $("#toast");
  el.textContent = msg; el.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.add("hidden"), 3500);
}
const fail = (e) => toast(T(e.message) !== e.message ? T(e.message) : (T("error") + ": " + e.message), true);

// ------------------------------------------------------------------ theme & language
function applyLang() {
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  $("#langBtn").textContent = lang === "ar" ? "EN" : "ع";
}
function applyTheme() {
  document.documentElement.dataset.theme = theme;
  $("#themeBtn").textContent = theme === "dark" ? "☀" : "☾";
}

// ------------------------------------------------------------------ auth screens
async function showAuth() {
  $("#app").classList.add("hidden");
  const box = $("#auth");
  box.classList.remove("hidden");
  const h = await fetch("/api/health").then((r) => r.json()).catch(() => ({}));
  if (h.setup_needed) {
    box.innerHTML = `<form class="card" id="f"><h2>${T("setup_title")}</h2><p class="muted small">${T("setup_hint")}</p>
      <label>${T("setup_key")}</label><input type="text" name="setup_key" required autocomplete="off">
      <label>${T("username")}</label><input type="text" name="username" required minlength="3">
      <label>${T("password")}</label><input type="password" name="password" required minlength="8">
      <p></p><button class="btn primary">${T("create_account")}</button> <span class="small" id="msg"></span></form>`;
    api("/auth/local-setup-key").then((r) => { $("#f [name=setup_key]").value = r.setup_key; }).catch(() => {});
    $("#f").onsubmit = async (ev) => {
      ev.preventDefault();
      const fd = Object.fromEntries(new FormData(ev.target));
      try { await api("/auth/setup", { method: "POST", body: fd }); await doLogin(fd.username, fd.password, ""); }
      catch (e) { $("#msg").textContent = T(e.message); }
    };
  } else {
    box.innerHTML = `<form class="card" id="f"><h2>${T("login_title")}</h2><p class="muted small">ULTRA AI GOD MODE</p>
      <label>${T("username")}</label><input type="text" name="username" required autocomplete="username">
      <label>${T("password").split(" (")[0]}</label><input type="password" name="password" required autocomplete="current-password">
      <div id="codeBox" class="hidden"><label>${T("twofa_code")}</label><input type="text" name="code" inputmode="numeric" autocomplete="one-time-code"></div>
      <label>${T("device_name")}</label><input type="text" name="device_name" value="${esc(store.get("device", guessDevice()))}">
      <p></p><button class="btn primary">${T("login")}</button> <span class="small" id="msg"></span>
      <p class="small"><a href="#" id="sw">${lang === "ar" ? "English" : "العربية"}</a> · <a href="#" id="forgot">${T("forgot")}</a></p></form>`;
    $("#forgot").onclick = (e) => {
      e.preventDefault();
      box.innerHTML = `<form class="card" id="rf"><h2>${T("forgot")}</h2><p class="muted small">${T("reset_hint")}</p>
        <label>${T("setup_key")}</label><input type="text" name="setup_key" required autocomplete="off">
        <label>${T("new_password")}</label><input type="password" name="password" required minlength="8">
        <p></p><button class="btn primary">${T("save")}</button> <span class="small" id="msg"></span></form>`;
      $("#rf").onsubmit = async (ev) => {
        ev.preventDefault();
        try { await api("/auth/reset", { method: "POST", body: Object.fromEntries(new FormData(ev.target)) }); toast(T("saved")); showAuth(); }
        catch (x) { $("#msg").textContent = T(x.message); }
      };
    };
    $("#sw").onclick = (e) => { e.preventDefault(); lang = lang === "ar" ? "en" : "ar"; store.set("lang", lang); applyLang(); showAuth(); };
    $("#f").onsubmit = async (ev) => {
      ev.preventDefault();
      const fd = Object.fromEntries(new FormData(ev.target));
      store.set("device", fd.device_name);
      try { await doLogin(fd.username, fd.password, fd.code || "", fd.device_name); }
      catch (e) { if (e.message === "2fa_required") $("#codeBox").classList.remove("hidden"); $("#msg").textContent = T(e.message); }
    };
  }
}
function guessDevice() { const u = navigator.userAgent; return /Android/i.test(u) ? "Android" : /Windows/i.test(u) ? "Windows PC" : /iPhone|iPad/i.test(u) ? "iPhone" : "Browser"; }
async function doLogin(username, password, code, device_name = guessDevice()) {
  const kind = /UltraAI-Android/.test(navigator.userAgent) ? "android" : /UltraAI-Windows/.test(navigator.userAgent) ? "windows" : "browser";
  const r = await api("/auth/login", { method: "POST", body: { username, password, code, device_name, kind } });
  if (kind !== "browser" && r.token) store.set("token", r.token);
  start();
}

// ------------------------------------------------------------------ navigation
const PAGES = [
  ["overview", "◎"], ["command", "✦"], ["tasks", "☰"], ["approvals", "⚠"], ["agents", "◈"], ["projects", "▦"],
  ["memory", "✎"], ["files", "🗀"], ["telegram", "✈"], ["devices", "⇄"], ["modules", "🎮"], ["security", "🛡"],
  ["models", "⚙"], ["reports", "📊"], ["settings", "⚒"],
];
let current = "overview";
let state = null;
let timer = null;

function renderNav() {
  const pending = state ? (state.counts.needs_approval || 0) : 0;
  $("#nav").innerHTML = PAGES.map(([k, ic]) => `<a data-p="${k}" class="${k === current ? "active" : ""}"><span class="ic">${ic}</span>${T("nav_" + k)}
    ${k === "approvals" && pending ? `<span class="count">${pending}</span>` : ""}</a>`).join("");
  $("#nav").querySelectorAll("a").forEach((a) => a.onclick = () => go(a.dataset.p));
}
function go(p) {
  current = p; location.hash = p; $("#sidebar").classList.remove("open");
  renderNav(); $("#pageTitle").textContent = T("nav_" + p);
  render();
}

async function poll() {
  try {
    state = await api("/state");
    $("#conn").className = "conn"; $("#conn span").textContent = T("online");
    $("#version").textContent = "v" + state.version;
    const em = $("#emergencyBtn");
    em.textContent = state.emergency ? T("emergency_release") : T("emergency");
    em.classList.toggle("on", state.emergency);
    $("#banner").classList.toggle("hidden", !state.emergency);
    $("#banner").textContent = T("emergency_banner");
    renderNav();
    if (["overview", "approvals"].includes(current) && !document.activeElement.matches("input,textarea,select")) render(true);
    if (current === "command") refreshChat();
  } catch (e) {
    if (e.message !== "login_required") { $("#conn").className = "conn off"; $("#conn span").textContent = T("offline"); }
  }
}

async function start() {
  $("#auth").classList.add("hidden");
  $("#app").classList.remove("hidden");
  current = (location.hash.slice(1).split("?")[0]) || "overview";
  if (!PAGES.find((p) => p[0] === current)) current = "overview";
  await poll();
  go(current);
  clearInterval(timer); timer = setInterval(poll, 2500);
}

// ------------------------------------------------------------------ pages
const pages = {};

async function render(quiet = false) {
  const el = $("#page");
  try { await pages[current](el, quiet); } catch (e) { if (e.message !== "login_required") el.innerHTML = `<div class="empty">${esc(T("error"))}: ${esc(e.message)}</div>`; }
}

function taskItem(t) {
  return `<div class="item click" data-task="${t.id}"><span class="grow"><div class="title">${esc(t.title)}</div>
    <div class="small muted">${fmtTime(t.created_at)} · ${esc(t.source)} · #${t.id}</div>
    <div class="progress"><i style="width:${t.progress}%"></i></div></span>${badge(t.status)}</div>`;
}
function bindTasks(el) { el.querySelectorAll("[data-task]").forEach((x) => x.onclick = () => openTask(x.dataset.task)); }

pages.overview = async (el) => {
  const s = state;
  const sys = s.system;
  const prov = Object.entries(s.providers).filter(([, v]) => v).map(([k]) => k);
  const tg = s.telegram;
  const tgState = !tg.enabled || !tg.token ? `<span class="dot"></span> ${T("tg_off")}` : tg.connected ? `<span class="dot ok"></span> ${T("tg_on")} @${esc(tg.username)}` : `<span class="dot bad"></span> ${T("tg_err")}`;
  const agentCounts = Object.values(s.agents).reduce((a, v) => (a[v] = (a[v] || 0) + 1, a), {});
  el.innerHTML = `
  <div class="grid">
    <div class="card"><h3><span class="dot ${s.emergency ? "bad" : "ok"}"></span>${T("system")}</h3>
      <div class="small">${T("cpu")}: <b>${sys.cpu_percent ?? "—"}%</b> · ${T("ram")}: <b>${sys.ram_percent ?? "—"}%</b> · ${T("disk")}: <b>${sys.disk_percent ?? "—"}%</b></div>
      <div class="small muted">${esc(sys.os)} · ${T("uptime")}: ${esc(sys.uptime)}</div></div>
    <div class="card"><h3>${T("models")}</h3><div>${prov.length ? prov.map((p) => `<span class="badge b-ok">${p}</span>`).join(" ") : `<span class="small muted">${T("none_configured")}</span>`}</div></div>
    <div class="card"><h3>${T("telegram")}</h3><div class="small">${tgState}</div><div class="small muted">${T("paired_users")}: ${tg.users}</div></div>
    <div class="card"><h3>${T("month_cost")}</h3><div class="stat">$${(s.month_spend || 0).toFixed(2)}</div><div class="small muted">/ $${s.budget}</div></div>
  </div>
  <div class="grid wide" style="margin-top:14px">
    <div class="card"><h3>${T("active_tasks")} (${s.active.length})</h3><div class="list">${s.active.map(taskItem).join("") || `<div class="empty">${T("no_tasks")}</div>`}</div></div>
    <div class="card"><h3>${T("recent_tasks")}</h3><div class="list">${s.recent.slice(0, 8).map(taskItem).join("") || `<div class="empty">${T("no_tasks")}</div>`}</div></div>
  </div>
  <div class="card" style="margin-top:14px"><h3>${T("agents_status")}</h3>
    <div class="row small">${["ready", "partial", "planned"].map((k) => `<span class="shrink"><span class="badge b-${k}">${T("ag_" + k)}</span> ${agentCounts[k] || 0}</span>`).join("")}</div></div>`;
  bindTasks(el);
};

// ---- command & chat (with voice)
let recog = null;
pages.command = async (el) => {
  const speak = store.get("speak", false);
  const vlang = store.get("vlang", "ar-DZ");
  el.innerHTML = `<div class="card">
    <div class="composer"><textarea id="cmd" placeholder="${esc(T("command_ph"))}"></textarea>
      <button class="mic" id="mic" title="${T("voice_title")}">🎤</button></div>
    <div class="row" style="margin-top:10px">
      <label class="switch shrink"><input type="checkbox" id="speak" ${speak ? "checked" : ""}> ${T("speak_replies")}</label>
      <select id="vlang" class="shrink" style="width:auto">${[["ar-DZ", "العربية (الجزائر)"], ["ar-SA", "العربية الفصحى"], ["fr-FR", "Français"], ["en-US", "English"]].map(([v, n]) => `<option value="${v}" ${v === vlang ? "selected" : ""}>${n}</option>`).join("")}</select>
      <span id="recInd" class="rec hidden shrink">● ${T("listening")}</span>
      <span></span>
      <label class="btn shrink">${T("attach")}<input type="file" id="file" class="hidden"></label>
      <button class="btn primary shrink" id="send">${T("send")}</button>
    </div>
    <div id="fileName" class="small muted"></div>
    <p class="small muted">${T("offline_cmds")}<br>${T("mic_note")}</p></div>
  <div class="chat" id="chat"></div>`;
  $("#speak").onchange = (e) => store.set("speak", e.target.checked);
  $("#vlang").onchange = (e) => store.set("vlang", e.target.value);
  $("#file").onchange = (e) => $("#fileName").textContent = e.target.files[0] ? "📎 " + e.target.files[0].name : "";
  $("#cmd").onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(); };
  $("#send").onclick = send;
  $("#mic").onclick = toggleMic;
  chatSeen = {};
  refreshChat();
};
async function send() {
  const text = $("#cmd").value.trim();
  const f = $("#file").files[0];
  if (!text && !f) return;
  stopMic();
  try {
    if (f) {
      const fd = new FormData(); fd.append("file", f); fd.append("text", text || (f.type.startsWith("image/") ? (lang === "ar" ? "حلل هذه الصورة" : "Analyse this image") : (lang === "ar" ? "لخص هذا الملف" : "Summarise this file")));
      await api("/upload", { method: "POST", body: fd });
      $("#file").value = ""; $("#fileName").textContent = "";
    } else {
      await api("/tasks", { method: "POST", body: { text } });
    }
    $("#cmd").value = "";
    refreshChat();
  } catch (e) { fail(e); }
}
let chatSeen = {};
async function refreshChat() {
  const box = $("#chat");
  if (!box) return;
  const tasks = (await api("/tasks?limit=20")).reverse();
  const full = await Promise.all(tasks.map((t) => ["completed", "failed", "needs_approval"].includes(t.status) ? api("/tasks/" + t.id) : t));
  box.innerHTML = full.map((t) => {
    let reply = "";
    if (t.status === "completed") reply = t.result || "";
    else if (t.status === "failed") reply = "❌ " + (t.error || "");
    else if (t.status === "needs_approval") reply = "⚠️ " + (t.approval_reason || "");
    else reply = "⏳ " + T("working");
    return `<div class="bubble me">${esc(t.description || t.title)}</div>
      <div class="bubble ai" data-task="${t.id}" style="cursor:pointer">${esc(reply)}<div class="small muted">${badge(t.status)} #${t.id}</div></div>`;
  }).join("");
  bindTasks(box);
  if (store.get("speak", false)) {
    full.filter((t) => t.status === "completed" && chatSeen[t.id] === "running").forEach((t) => say(t.result || ""));
  }
  full.forEach((t) => chatSeen[t.id] = ["pending", "running"].includes(t.status) ? "running" : t.status);
}
function say(text) {
  if (!("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text.slice(0, 1500));
  u.lang = store.get("vlang", "ar-DZ");
  speechSynthesis.cancel(); speechSynthesis.speak(u);
}
function toggleMic() { recog ? stopMic() : startMic(); }
function startMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return toast(T("mic_unsupported"), true);
  recog = new SR();
  recog.lang = store.get("vlang", "ar-DZ");
  recog.continuous = true; recog.interimResults = true;
  const base = $("#cmd").value;
  recog.onresult = (e) => {
    let txt = "";
    for (let i = 0; i < e.results.length; i++) txt += e.results[i][0].transcript;
    $("#cmd").value = (base ? base + " " : "") + txt;
  };
  recog.onerror = (e) => { toast("🎤 " + e.error, true); stopMic(); };
  recog.onend = () => stopMic();
  recog.start();
  $("#mic").classList.add("on"); $("#mic").title = T("stop_listening"); $("#recInd").classList.remove("hidden");
}
function stopMic() {
  if (recog) { const r = recog; recog = null; try { r.stop(); } catch {} }
  const m = $("#mic"); if (m) { m.classList.remove("on"); m.title = T("voice_title"); }
  const ri = $("#recInd"); if (ri) ri.classList.add("hidden");
}

// ---- tasks
let taskFilter = "active";
pages.tasks = async (el) => {
  const tabs = ["active", "all", "pending", "running", "paused", "needs_approval", "completed", "failed", "stopped"];
  const list = await api("/tasks?limit=200" + (taskFilter !== "all" ? "&status=" + taskFilter : ""));
  el.innerHTML = `<div class="tabs">${tabs.map((k) => `<button data-f="${k}" class="${k === taskFilter ? "active" : ""}">${T("st_" + k)}</button>`).join("")}</div>
  <div class="card"><div class="table-wrap"><table><thead><tr><th>${T("task")}</th><th>${T("status")}</th><th>${T("progress")}</th><th>${T("started")}</th><th>${T("source")}</th></tr></thead><tbody>
  ${list.map((t) => `<tr class="item-row" data-task="${t.id}" style="cursor:pointer"><td>${esc(t.title)}<div class="small muted">#${t.id}</div></td><td>${badge(t.status)}${t.verified ? " ✓" : ""}</td>
    <td style="min-width:90px"><div class="progress"><i style="width:${t.progress}%"></i></div><span class="small muted">${t.progress}%</span></td><td class="small">${fmtTime(t.created_at)}</td><td class="small">${esc(t.source)}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">${T("no_tasks")}</td></tr>`}
  </tbody></table></div></div>`;
  el.querySelectorAll("[data-f]").forEach((b) => b.onclick = () => { taskFilter = b.dataset.f; render(); });
  bindTasks(el);
};

pages.approvals = async (el) => {
  const list = await api("/tasks?status=needs_approval");
  el.innerHTML = `<div class="card"><p class="muted small">${T("approvals_text")}</p></div>
  <div class="list" style="margin-top:14px">${list.map((t) => `<div class="card"><h3>${esc(t.title)} <span class="small muted">#${t.id}</span></h3>
    <p>${esc(t.approval_reason || "")}</p><div class="row">${actionButtons(t)}<button class="btn shrink" data-task="${t.id}">${T("steps")}…</button></div></div>`).join("") || `<div class="empty">${T("no_tasks")}</div>`}</div>`;
  bindActions(el); bindTasks(el);
};

function actionButtons(t) {
  const b = (a, cls = "") => `<button class="btn small ${cls} shrink" data-act="${a}" data-id="${t.id}">${T(a)}</button>`;
  const clar = t.plan && t.plan.clarification;
  const out = [];
  if (t.status === "needs_approval" && !clar) out.push(b("approve", "ok"), b("deny", "danger"));
  if (t.status === "needs_approval" && clar) out.push(`<input type="text" class="ans" data-id="${t.id}" placeholder="${esc(T("answer_ph"))}">`, b("answer", "primary"));
  if (["pending", "running"].includes(t.status)) out.push(b("pause"));
  if (t.status === "paused") out.push(b("resume", "primary"));
  if (["pending", "running", "paused", "needs_approval"].includes(t.status)) out.push(b("stop", "danger"));
  if (["failed", "stopped"].includes(t.status)) out.push(b("retry", "primary"));
  return out.join("");
}
function bindActions(root, after) {
  root.querySelectorAll("[data-act]").forEach((btn) => btn.onclick = async (e) => {
    e.stopPropagation();
    const { act, id } = btn.dataset;
    try {
      if (act === "answer") {
        const inp = root.querySelector(`.ans[data-id="${id}"]`);
        await api(`/tasks/${id}/answer`, { method: "POST", body: { text: inp.value } });
      } else await api(`/tasks/${id}/${act}`, { method: "POST" });
      toast("👍"); await poll(); after ? after() : render();
    } catch (err) { fail(err); }
  });
}

async function openTask(id) {
  const t = await api("/tasks/" + id);
  const plan = t.plan || {};
  const icon = { done: "✔️", failed: "❌", running: "⚙️", pending: "•" };
  $("#drawerBody").innerHTML = `<h2 style="margin-top:0">${esc(t.title)}</h2>
    <div class="row small"><span class="shrink">${badge(t.status)}</span><span class="shrink">#${t.id}</span><span class="shrink">${T("started")}: ${fmtTime(t.started_at || t.created_at)}</span>
      <span class="shrink">${t.verified ? "✅ " + T("verified") : t.status === "completed" ? "" : ""}</span></div>
    <div class="progress" style="margin:10px 0"><i style="width:${t.progress}%"></i></div>
    <div class="row">${actionButtons(t)}</div>
    ${t.approval_reason ? `<div class="card" style="margin-top:12px;border-color:var(--warn)"><b>${T("approval_needed")}</b><p>${esc(t.approval_reason)}</p></div>` : ""}
    <label>${T("description")}</label><div class="result">${esc(t.description)}</div>
    <label>${T("steps")} ${plan.planner ? `<span class="small muted">(${T("planner")}: ${esc(plan.planner)})</span>` : ""}</label>
    <ul class="steps">${(t.steps || []).map((s, i) => `<li><span>${icon[s.status] || "•"}</span><span class="grow"><b>${i + 1}. ${esc(s.description)}</b>
      <span class="small muted">${esc(s.agent)}.${esc(s.action)}</span>${s.error ? `<div class="small" style="color:var(--bad)">${esc(s.error)}</div>` : ""}</span></li>`).join("") || `<li class="muted">—</li>`}</ul>
    ${(plan.warnings || []).length ? `<label>${T("warnings")}</label><div class="result small">${esc(plan.warnings.join("\n"))}</div>` : ""}
    ${t.error ? `<label>${T("errors")}</label><div class="result" style="border-color:var(--bad)">${esc(t.error)}</div>` : ""}
    ${t.result ? `<label>${T("result")}</label><div class="result">${esc(t.result)}</div>` : ""}
    ${(t.files || []).length ? `<label>${T("files_out")}</label><div class="list">${t.files.map((f) => `<a class="item" href="/api/files/download?path=${encodeURIComponent(f)}">📄 ${esc(f)}</a>`).join("")}</div>` : ""}
    <label>${T("exec_log")}</label><div class="result log">${t.logs.map((l) => `<div class="${l.level}">${new Date(l.ts * 1000).toLocaleTimeString("en-GB")} [${l.level}] ${esc(l.message)}</div>`).join("")}</div>`;
  $("#drawer").classList.remove("hidden");
  bindActions($("#drawerBody"), () => openTask(id));
}

// ---- agents
pages.agents = async (el) => {
  const agents = await api("/agents");
  el.innerHTML = `<div class="grid wide">${agents.map((a) => `<div class="card"><h3>${esc(lang === "ar" ? a.name_ar : a.name_en)} <span class="badge b-${a.status}">${T("ag_" + a.status)}</span>
    ${a.phase && a.status !== "ready" ? `<span class="small muted">${T("phase")} ${a.phase}</span>` : ""}</h3>
    <p class="small muted">${esc(lang === "ar" ? a.note_ar : a.note_en)}</p>
    ${a.actions.length ? `<div class="small"><b>${T("actions")}:</b> ${a.actions.map((x) => `<span class="badge ${x.risk === "approval" ? "b-warn" : ""}" title="${esc(x.description)}">${esc(x.name)}${x.risk === "approval" ? " ⚠" : ""}</span>`).join(" ")}</div>` : ""}</div>`).join("")}</div>`;
};

// ---- projects
pages.projects = async (el) => {
  const ps = await api("/projects");
  const today = new Date().toISOString().slice(0, 10);
  el.innerHTML = `<div class="card"><h3>${T("new_project")}</h3><form class="row" id="np">
    <input type="text" name="name" placeholder="${T("project_name")}" required><input type="text" name="description" placeholder="${T("description")}">
    <input type="date" name="deadline" class="shrink" style="width:auto" title="${T("deadline")}"><button class="btn primary">${T("add")}</button></form></div>
  ${ps.map((p) => {
    const done = p.items.filter((i) => i.status === "done").length;
    return `<div class="card"><h3>${esc(p.name)} <span class="small muted">${done}/${p.items.length}</span>${p.deadline ? `<span class="badge ${p.deadline < today ? "b-bad" : ""}">${esc(p.deadline)}</span>` : ""}
      <span style="flex:1"></span><button class="btn small danger" data-delp="${p.id}">${T("delete")}</button></h3>
      ${p.description ? `<p class="small muted">${esc(p.description)}</p>` : ""}
      <div class="progress"><i style="width:${p.items.length ? (100 * done / p.items.length) : 0}%"></i></div>
      <div class="list" style="margin-top:10px">${p.items.map((i) => `<div class="item"><input type="checkbox" data-item="${i.id}" ${i.status === "done" ? "checked" : ""}>
        <span class="grow" style="${i.status === "done" ? "text-decoration:line-through;opacity:.6" : ""}">${esc(i.title)}</span>
        <span class="badge ${i.priority === "high" ? "b-bad" : i.priority === "medium" ? "b-warn" : ""}">${T("p_" + i.priority)}</span>
        ${i.due ? `<span class="small ${i.due < today && i.status !== "done" ? "" : "muted"}" style="${i.due < today && i.status !== "done" ? "color:var(--bad)" : ""}">${esc(i.due)}</span>` : ""}
        <button class="icon-btn" data-deli="${i.id}">✕</button></div>`).join("")}</div>
      <form class="row" data-addi="${p.id}" style="margin-top:8px"><input type="text" name="title" placeholder="${T("add_item")}" required>
        <select name="priority" class="shrink" style="width:auto"><option value="high">${T("p_high")}</option><option value="medium" selected>${T("p_medium")}</option><option value="low">${T("p_low")}</option></select>
        <input type="date" name="due" class="shrink" style="width:auto"><button class="btn">${T("add")}</button></form></div>`;
  }).join("") || `<div class="empty">${T("no_projects")}</div>`}`;
  $("#np").onsubmit = async (e) => { e.preventDefault(); try { await api("/projects", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); render(); } catch (x) { fail(x); } };
  el.querySelectorAll("[data-addi]").forEach((f) => f.onsubmit = async (e) => { e.preventDefault(); await api(`/projects/${f.dataset.addi}/items`, { method: "POST", body: Object.fromEntries(new FormData(f)) }); render(); });
  el.querySelectorAll("[data-item]").forEach((c) => c.onchange = async () => { await api(`/items/${c.dataset.item}`, { method: "PATCH", body: { status: c.checked ? "done" : "todo" } }); render(); });
  el.querySelectorAll("[data-deli]").forEach((b) => b.onclick = async () => { if (confirm(T("confirm_delete"))) { await api(`/items/${b.dataset.deli}`, { method: "DELETE" }); render(); } });
  el.querySelectorAll("[data-delp]").forEach((b) => b.onclick = async () => { if (confirm(T("confirm_delete"))) { await api(`/projects/${b.dataset.delp}`, { method: "DELETE" }); render(); } });
};

// ---- memory
let memQ = "", memScope = "";
pages.memory = async (el) => {
  const rows = await api(`/memory?q=${encodeURIComponent(memQ)}&scope=${memScope}`);
  el.innerHTML = `<div class="card"><p class="small muted">${T("memory_hint")}</p>
    <form class="row" id="nm"><input type="text" name="content" placeholder="${lang === "ar" ? "مثال: أفضل الردود المختصرة" : "e.g. I prefer short answers"}" required>
    <select name="scope" class="shrink" style="width:auto"><option value="personal">${T("scope_personal")}</option><option value="project">${T("scope_project")}</option></select>
    <input type="text" name="project" class="shrink" style="width:160px" placeholder="${T("project_name")}"><button class="btn primary">${T("save")}</button></form></div>
  <div class="card"><div class="row"><input type="text" id="mq" value="${esc(memQ)}" placeholder="${T("search")}">
    <div class="tabs shrink" style="margin:0">${[["", T("st_all")], ["personal", T("scope_personal")], ["project", T("scope_project")]].map(([v, n]) => `<button data-sc="${v}" class="${v === memScope ? "active" : ""}">${n}</button>`).join("")}</div></div>
    <div class="list" style="margin-top:10px">${rows.map((r) => `<div class="item" data-mid="${r.id}"><span class="grow"><span class="mtext">${esc(r.content)}</span>
      <div class="small muted">${T("scope_" + r.scope)}${r.project ? " · " + esc(r.project) : ""} · ${fmtTime(r.updated_at)}</div></span>
      <button class="btn small shrink" data-edit="${r.id}">${T("edit")}</button><button class="btn small danger shrink" data-del="${r.id}">${T("delete")}</button></div>`).join("") || `<div class="empty">—</div>`}</div></div>`;
  $("#nm").onsubmit = async (e) => { e.preventDefault(); try { await api("/memory", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); render(); } catch (x) { fail(x); } };
  $("#mq").oninput = (e) => { memQ = e.target.value; clearTimeout(pages.memory.t); pages.memory.t = setTimeout(render, 300); };
  el.querySelectorAll("[data-sc]").forEach((b) => b.onclick = () => { memScope = b.dataset.sc; render(); });
  el.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => { if (confirm(T("confirm_delete"))) { await api("/memory/" + b.dataset.del, { method: "DELETE" }); render(); } });
  el.querySelectorAll("[data-edit]").forEach((b) => b.onclick = async () => {
    const r = rows.find((x) => x.id == b.dataset.edit);
    const v = prompt(T("edit"), r.content);
    if (v && v !== r.content) { try { await api("/memory/" + r.id, { method: "PUT", body: { content: v, scope: r.scope, project: r.project || "" } }); render(); } catch (x) { fail(x); } }
  });
};

// ---- files
let folder = "";
pages.files = async (el) => {
  const rows = await api("/files?folder=" + encodeURIComponent(folder));
  el.innerHTML = `<div class="card"><p class="small muted">${T("files_hint")}</p><div class="row"><span class="kbd shrink">/${esc(folder)}</span><span></span>
    ${folder ? `<button class="btn shrink" id="up">${T("up")}</button>` : ""}</div>
    <div class="list" style="margin-top:10px">${rows.map((r) => r.dir ? `<div class="item click" data-dir="${esc(r.path)}">📁 <span class="grow">${esc(r.name)}</span></div>`
      : `<a class="item" href="/api/files/download?path=${encodeURIComponent(r.path)}">📄 <span class="grow">${esc(r.name)}</span><span class="small muted">${(r.size / 1024).toFixed(1)} KB · ${fmtTime(r.mtime)}</span></a>`).join("") || `<div class="empty">—</div>`}</div></div>`;
  el.querySelectorAll("[data-dir]").forEach((d) => d.onclick = () => { folder = d.dataset.dir; render(); });
  const up = $("#up"); if (up) up.onclick = () => { folder = folder.split("/").slice(0, -1).join("/"); render(); };
};

// ---- telegram
pages.telegram = async (el) => {
  const [tg, sec, st] = await Promise.all([api("/telegram"), api("/secrets"), api("/settings")]);
  el.innerHTML = `<div class="grid wide">
    <div class="card"><h3>${T("tg_steps")}</h3><ol class="small"><li>${T("tg_s1")}</li><li>${T("tg_s2")}</li><li>${T("tg_s3")}</li></ol>
      <label>${T("bot_token")} <span class="badge ${sec.telegram_bot_token.set ? "b-ok" : ""}">${sec.telegram_bot_token.set ? T("set") + " " + esc(sec.telegram_bot_token.hint) : T("not_set")}</span></label>
      <div class="row"><input type="password" id="tok" placeholder="123456789:AA…" autocomplete="off"><button class="btn shrink" id="saveTok">${T("save")}</button><button class="btn shrink" id="testTok">${T("test")}</button></div>
      <p><label class="switch"><input type="checkbox" id="tgEn" ${tg.enabled ? "checked" : ""}> ${T("enable_bot")}</label></p>
      <p><label class="switch"><input type="checkbox" id="tgNo" ${st.telegram_notify ? "checked" : ""}> ${T("notify_all")}</label></p>
      <div class="small">${!tg.enabled || !tg.token ? `<span class="dot"></span> ${T("tg_off")}` : tg.connected ? `<span class="dot ok"></span> ${T("tg_on")} @${esc(tg.username)}` : `<span class="dot bad"></span> ${T("tg_err")} ${esc(tg.error)}`}</div></div>
    <div class="card"><h3>${T("pair_code")}</h3><button class="btn primary" id="code">${T("pair_code")}</button><div id="codeOut"></div>
      <h3 style="margin-top:16px">${T("paired_users")}</h3><div class="list">${tg.users.map((u) => `<div class="item"><span class="grow">${esc(u.name || "")} ${u.username ? "@" + esc(u.username) : ""} <span class="small muted">ID ${u.user_id} · ${fmtTime(u.paired_at)}</span></span>
      <button class="btn small danger shrink" data-rm="${u.user_id}">${T("remove")}</button></div>`).join("") || `<div class="empty">—</div>`}</div></div></div>`;
  $("#saveTok").onclick = async () => { await api("/secrets", { method: "PUT", body: { name: "telegram_bot_token", value: $("#tok").value } }); toast(T("saved")); render(); };
  $("#testTok").onclick = async () => { const r = await api("/telegram/test", { method: "POST" }); toast(r.ok ? "✅ @" + r.username : "❌ " + r.error, !r.ok); };
  $("#tgEn").onchange = async (e) => { await api("/settings", { method: "PUT", body: { telegram_enabled: e.target.checked } }); toast(T("saved")); };
  $("#tgNo").onchange = async (e) => { await api("/settings", { method: "PUT", body: { telegram_notify: e.target.checked } }); toast(T("saved")); };
  $("#code").onclick = async () => { const r = await api("/telegram/code", { method: "POST" }); $("#codeOut").innerHTML = `<div class="code-big">${r.code}</div><div class="small muted" style="text-align:center">/pair ${r.code} — ${T("code_valid")}</div>`; };
  el.querySelectorAll("[data-rm]").forEach((b) => b.onclick = async () => { if (confirm(T("confirm_delete"))) { await api("/telegram/users/" + b.dataset.rm, { method: "DELETE" }); render(); } });
};

// ---- devices (PC & phone) and upcoming modules: honest status only
const MODULES = {
  ar: {
    pc: ["التحكم بالكمبيوتر", 4, "Windows 10/11، أذونات التحكم بالشاشة والماوس، وموافقتك على كل برنامج.", "فهم الشاشة، الماوس ولوحة المفاتيح، فتح وإغلاق البرامج، أتمتة المهام المتكررة."],
    browser: ["وكيل المتصفح", 4, "متصفح Chromium يديره النظام، ومواقع تسمح بالأتمتة.", "البحث والتعبئة والتنقل في المواقع المسموح بها."],
    phone: ["التحكم بالهاتف", 5, "تطبيق Android الخاص بالنظام، وأذونات تمنحها بنفسك (الإشعارات، إمكانية الوصول).", "فتح التطبيقات، قراءة الإشعارات المسموح بها، الملفات والصور، مزامنة المهام."],
    gaming: ["مركز الألعاب", 9, "ألعاب فردية تسمح بالأتمتة. لا تجاوز لأنظمة مكافحة الغش ولا لشروط الاستخدام.", "تحليل الشاشة، اقتراحات، أوامر متكررة مسموح بها، إعدادات لكل لعبة."],
    home: ["المنزل الذكي", 9, "Home Assistant أو منصة رسمية متوافقة، وأجهزة متوافقة.", "الإضاءة، المكيفات، المقابس، الحساسات، السيناريوهات. الأقفال وأجهزة السلامة بموافقة إضافية."],
  },
  en: {
    pc: ["PC control", 4, "Windows 10/11, screen and input permissions, and your approval per app.", "Screen understanding, mouse and keyboard, opening/closing apps, repetitive task automation."],
    browser: ["Browser agent", 4, "A Chromium browser driven by the system; sites that allow automation.", "Search, form filling and navigation on allowed sites."],
    phone: ["Phone control", 5, "The system's Android app and permissions you grant (notifications, accessibility).", "Open apps, read allowed notifications, files and photos, task sync."],
    gaming: ["Gaming center", 9, "Single-player games that allow automation. No anti-cheat or terms-of-service bypass.", "Screen analysis, tips, allowed repetitive commands, per-game settings."],
    home: ["Smart home", 9, "Home Assistant or an official compatible platform, and compatible devices.", "Lights, AC, plugs, sensors, scenes. Locks and safety devices need extra approval."],
  },
};
function moduleCard(key) {
  const [name, phase, req, what] = MODULES[lang][key];
  return `<div class="card"><h3>${name} <span class="badge b-planned">${T("not_built")} · ${T("phase")} ${phase}</span></h3>
    <p class="small">${what}</p><p class="small muted"><b>${T("requires")}:</b> ${req}</p></div>`;
}
pages.devices = async (el) => {
  const devs = await api("/devices");
  const active = devs.filter((d) => !d.revoked);
  el.innerHTML = `<div class="grid wide"><div class="card"><h3>${T("hub_title")}</h3><p class="small">${T("hub_text")}</p>
      <div class="list">${active.map((d) => `<div class="item"><span>${d.kind === "android" ? "📱" : d.kind === "windows" ? "🖥" : "🌐"}</span><span class="grow">${esc(d.name)} ${d.current ? `<span class="badge b-ok">${T("this_device")}</span>` : ""}<div class="small muted">${T("last_seen")}: ${fmtTime(d.last_seen)}</div></span></div>`).join("")}</div></div>
    <div class="card"><h3>${T("lan_title")}</h3><p class="small">${T("lan_text")}</p></div></div>
    <div class="grid wide" style="margin-top:14px">${["pc", "browser", "phone"].map(moduleCard).join("")}</div>`;
};
pages.modules = async (el) => {
  el.innerHTML = `<div class="grid wide">${["gaming", "home"].map(moduleCard).join("")}
    <div class="card"><h3>${T("voice_title")} <span class="badge b-partial">${T("ag_partial")}</span></h3>
    <p class="small">${lang === "ar" ? "يعمل الآن: الميكروفون وقراءة الردود بصوت في صفحة «الأوامر» (Chrome/Edge)، والرسائل الصوتية في Telegram (تحتاج مفتاح OpenAI)." : "Works now: microphone and spoken replies on the Command page (Chrome/Edge), and Telegram voice notes (need an OpenAI key)."}</p>
    <button class="btn" onclick="location.hash='command';location.reload()">${T("nav_command")}</button></div></div>`;
};

// ---- security
pages.security = async (el, quiet) => {
  const [me, devs, audit] = await Promise.all([api("/me"), api("/devices"), api("/security/audit" + (store.get("auditWarn", false) ? "?level=warn" : ""))]);
  el.innerHTML = `<div class="grid wide">
    <div class="card"><h3>${T("approvals_policy")}</h3><p class="small">${T("approvals_text")}</p>
      <button class="btn danger" id="em2">${state.emergency ? T("emergency_release") : T("emergency")}</button></div>
    <div class="card"><h3>${T("account")}: ${esc(me.user.username)}</h3>
      <div class="small">${T("twofa")}: <b>${me.user.twofa ? T("twofa_on") : T("twofa_off")}</b></div>
      <div id="tfa" style="margin-top:8px">${me.user.twofa ? `<div class="row"><input type="password" id="tfpw" placeholder="${T("old_password")}"><button class="btn shrink" id="tfoff">${T("disable_2fa")}</button></div>` : `<button class="btn primary" id="tfon">${T("enable_2fa")}</button>`}</div>
      <label>${T("change_password")}</label><div class="row"><input type="password" id="pw1" placeholder="${T("old_password")}"><input type="password" id="pw2" placeholder="${T("new_password")}"><button class="btn shrink" id="pwb">${T("save")}</button></div>
      <p><button class="btn" id="logout">${T("logout")}</button></p></div></div>
  <div class="card"><h3>${T("trusted_devices")}</h3><div class="table-wrap"><table><tbody>${devs.map((d) => `<tr><td>${esc(d.name)} ${d.current ? `<span class="badge b-ok">${T("this_device")}</span>` : ""}</td><td class="small">${esc(d.kind)}</td>
    <td class="small">${T("last_seen")}: ${fmtTime(d.last_seen)}</td><td>${d.revoked ? `<span class="badge b-bad">✕</span>` : `<button class="btn small danger" data-rev="${d.id}">${T("revoke")}</button>`}</td></tr>`).join("")}</tbody></table></div></div>
  <div class="card"><h3>${T("audit_log")} <span style="flex:1"></span><label class="switch small"><input type="checkbox" id="aw" ${store.get("auditWarn", false) ? "checked" : ""}> ${T("only_warnings")}</label></h3>
    <div class="table-wrap"><table><tbody>${audit.map((a) => `<tr><td class="small" style="white-space:nowrap">${fmtTime(a.ts)}</td><td><span class="badge ${a.level === "info" ? "" : a.level === "warning" ? "b-warn" : "b-bad"}">${esc(a.level)}</span></td>
    <td class="small">${esc(a.actor)}</td><td class="small"><b>${esc(a.action)}</b> ${esc(a.detail || "")}</td></tr>`).join("")}</tbody></table></div></div>`;
  $("#em2").onclick = emergencyClick;
  $("#aw").onchange = (e) => { store.set("auditWarn", e.target.checked); render(); };
  $("#logout").onclick = async () => { await api("/auth/logout", { method: "POST" }); store.set("token", ""); location.reload(); };
  $("#pwb").onclick = async () => { try { await api("/auth/password", { method: "POST", body: { old: $("#pw1").value, new: $("#pw2").value } }); toast(T("saved")); } catch (e) { fail(e); } };
  el.querySelectorAll("[data-rev]").forEach((b) => b.onclick = async () => { if (confirm(T("revoke") + "?")) { await api("/devices/" + b.dataset.rev, { method: "DELETE" }); render(); } });
  const on = $("#tfon");
  if (on) on.onclick = async () => {
    const r = await api("/auth/2fa/start", { method: "POST" });
    $("#tfa").innerHTML = `<p class="small">${T("twofa_scan")}</p><div class="kbd">${esc(r.secret)}</div><p class="small muted" style="direction:ltr">${esc(r.uri)}</p>
      <div class="row"><input type="text" id="tfc" inputmode="numeric" placeholder="123456"><button class="btn primary shrink" id="tfok">${T("confirm")}</button></div>`;
    $("#tfok").onclick = async () => { try { await api("/auth/2fa/confirm", { method: "POST", body: { text: $("#tfc").value } }); toast(T("saved")); render(); } catch (e) { fail(e); } };
  };
  const off = $("#tfoff");
  if (off) off.onclick = async () => { try { await api("/auth/2fa/disable", { method: "POST", body: { text: $("#tfpw").value } }); toast(T("saved")); render(); } catch (e) { fail(e); } };
};

// ---- models & costs
const OLLAMA_SUGGESTED = ["qwen3:8b", "qwen3:4b", "qwen2.5vl", "qwen3-coder"];
async function ollamaPanel() {
  const box = $("#olBox");
  if (!box) return;
  let o;
  try { o = await api("/models/ollama"); } catch (e) { return; }
  if (!document.body.contains(box)) return;
  const fmtGB = (b) => (b / 1e9).toFixed(1);
  const pulls = o.pulls.map((p) => {
    const pct = p.total ? Math.floor((p.completed / p.total) * 100) : 0;
    const state = !p.done ? `${esc(p.status)} ${p.total ? `${pct}% (${fmtGB(p.completed)}/${fmtGB(p.total)} GB)` : ""}`
      : p.ok ? `✅ ${T("ol_done")}` : `❌ ${p.error === "ollama_not_running" ? T("ol_off") : esc(p.error)}`;
    return `<div class="small"><b>${esc(p.model)}</b>: ${state}${!p.done ? `<div class="bar"><span style="width:${pct}%"></span></div>` : ""}</div>`;
  }).join("");
  box.innerHTML = `<p class="small">${o.available ? `<span class="dot ok"></span> ${T("ol_on")}` : `<span class="dot bad"></span> ${T("ol_off")} <a href="https://ollama.com/download" target="_blank" rel="noopener">ollama.com</a>`}</p>
    <p class="small">${T("ollama_found")}: ${o.models.map((m) => `<span class="badge">${esc(m)}</span>`).join(" ") || "—"}</p>
    ${o.available ? `<p><button class="btn primary" id="olBest">${T("ol_best")} ${esc(o.recommended)}</button>
      <span class="small muted">${T("ol_ram")}: ${o.ram_gb} GB</span></p>` : `<p><a class="btn primary" href="https://ollama.com/download/OllamaSetup.exe" target="_blank" rel="noopener">${T("ol_get")}</a></p>`}
    <label>${T("ol_pull")}</label>
    <div class="row"><input type="text" id="olName" list="olList" placeholder="${esc(o.recommended)}" style="direction:ltr" autocomplete="off">
      <datalist id="olList">${OLLAMA_SUGGESTED.map((m) => `<option value="${m}">`).join("")}</datalist>
      <button class="btn primary shrink" id="olPull" ${o.available ? "" : "disabled"}>${T("ol_pull_btn")}</button></div>
    <p class="small muted">${T("ol_hint")}</p>${pulls}`;
  const pull = async (name) => {
    try { await api("/models/ollama/pull", { method: "POST", body: { text: name } }); ollamaPanel(); } catch (e) { fail(e); }
  };
  $("#olPull").onclick = () => pull($("#olName").value.trim() || o.recommended);
  const best = $("#olBest");
  if (best) best.onclick = () => pull(o.recommended);
  if (o.pulls.some((p) => !p.done)) setTimeout(ollamaPanel, 1500);
}
pages.models = async (el) => {
  const [sec, st, usage] = await Promise.all([api("/secrets"), api("/settings"), api("/usage")]);
  const routes = Object.keys(st.routes);
  const keyRow = (n, label) => `<label>${label} <span class="badge ${sec[n].set ? "b-ok" : ""}">${sec[n].set ? T("set") + " " + esc(sec[n].hint) : T("not_set")}</span></label>
    <div class="row"><input type="password" data-key="${n}" autocomplete="off"><button class="btn shrink" data-savekey="${n}">${T("save")}</button></div>`;
  el.innerHTML = `<div class="grid wide">
    <div class="card"><h3>${T("keys")}</h3>${keyRow("anthropic_api_key", T("key_anthropic"))}${keyRow("openai_api_key", T("key_openai"))}
      <label>${T("base_url")}</label><input type="url" id="obase" value="${esc(st.openai_base_url)}">
      <h3 style="margin-top:16px">${T("ollama")}</h3><input type="url" id="ourl" value="${esc(st.ollama_url)}">
      <div id="olBox"></div></div>
    <div class="card"><h3>${T("month_cost")}: $${usage.month_spend.toFixed(2)}</h3>
      <label>${T("budget")}</label><input type="number" id="budget" min="0" step="1" value="${st.monthly_budget_usd}">
      <p><label class="switch"><input type="checkbox" id="ws" ${st.web_search_enabled ? "checked" : ""}> ${T("web_search")}</label></p>
      <p><label class="switch"><input type="checkbox" id="cc" ${st.cross_check_enabled ? "checked" : ""}> ${T("cross_check")}</label></p>
      <h3>${T("usage")}</h3><div class="table-wrap"><table><thead><tr><th>${T("models")}</th><th>${T("calls")}</th><th>tokens</th><th>${T("cost")}</th></tr></thead><tbody>
      ${usage.by_model.map((u) => `<tr><td class="small">${esc(u.provider)}:${esc(u.model)}</td><td>${u.calls}${u.errors ? ` <span class="small" style="color:var(--bad)">(${u.errors}✕)</span>` : ""}</td><td class="small">${u.input_tokens}/${u.output_tokens}</td><td>$${u.cost_usd}</td></tr>`).join("") || `<tr><td colspan="4" class="empty">—</td></tr>`}</tbody></table></div></div></div>
  <div class="card"><h3>${T("routes")}</h3><p class="small muted">${T("route_hint")}</p><div class="grid">
    ${routes.map((r) => `<div><label>${T("rt_" + r)}</label><textarea data-route="${r}" style="min-height:80px;direction:ltr">${esc(st.routes[r].join("\n"))}</textarea>
      <div class="row"><select data-effort="${r}">${["low", "medium", "high", "xhigh", "max"].map((e) => `<option ${st.effort[r] === e ? "selected" : ""}>${e}</option>`).join("")}</select>
      <button class="btn small shrink" data-test="${r}">${T("test_route")}</button></div></div>`).join("")}</div>
    <p><button class="btn primary" id="saveModels">${T("save")}</button></p></div>`;
  ollamaPanel();
  el.querySelectorAll("[data-savekey]").forEach((b) => b.onclick = async () => {
    const n = b.dataset.savekey; await api("/secrets", { method: "PUT", body: { name: n, value: el.querySelector(`[data-key="${n}"]`).value } }); toast(T("saved")); render(); });
  el.querySelectorAll("[data-test]").forEach((b) => b.onclick = async () => {
    b.disabled = true; const r = await api("/models/test", { method: "POST", body: { text: b.dataset.test } }); b.disabled = false;
    toast(r.ok ? `✅ ${r.provider}:${r.model} — $${r.cost_usd}` : "❌ " + r.error, !r.ok); });
  $("#saveModels").onclick = async () => {
    const routesV = {}, effortV = {};
    el.querySelectorAll("[data-route]").forEach((t) => routesV[t.dataset.route] = t.value.split("\n").map((x) => x.trim()).filter(Boolean));
    el.querySelectorAll("[data-effort]").forEach((s) => effortV[s.dataset.effort] = s.value);
    await api("/settings", { method: "PUT", body: { routes: routesV, effort: effortV, monthly_budget_usd: Number($("#budget").value),
      web_search_enabled: $("#ws").checked, cross_check_enabled: $("#cc").checked, ollama_url: $("#ourl").value, openai_base_url: $("#obase").value } });
    toast(T("saved"));
  };
};

// ---- reports
pages.reports = async (el) => {
  const r = await api("/reports");
  const spark = (key, color) => {
    const pts = r.health.filter((h) => h[key] !== null);
    if (pts.length < 2) return "";
    const t0 = pts[0].ts, t1 = pts[pts.length - 1].ts || t0 + 1;
    const d = pts.map((h, i) => `${i ? "L" : "M"}${((h.ts - t0) / (t1 - t0 || 1) * 300).toFixed(1)},${(70 - h[key] * 0.7).toFixed(1)}`).join(" ");
    return `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.5"/>`;
  };
  el.innerHTML = `<div class="grid">
    <div class="card"><h3>${T("success_rate")}</h3><div class="stat">${r.success_rate === null ? "—" : Math.round(r.success_rate * 100) + "%"}</div></div>
    <div class="card"><h3>${T("avg_time")}</h3><div class="stat">${r.avg_seconds ? r.avg_seconds.toFixed(1) + "s" : "—"}</div></div>
    <div class="card"><h3>${T("month_cost")}</h3><div class="stat">$${r.month_spend.toFixed(2)}</div></div>
    ${Object.entries(r.by_status).map(([k, v]) => `<div class="card"><h3>${badge(k)}</h3><div class="stat">${v}</div></div>`).join("")}</div>
  <div class="grid wide" style="margin-top:14px">
    <div class="card"><h3>${T("suggestions")}</h3>${r.suggestions.map((s) => `<p class="small">💡 ${T("sg_" + s)}</p>`).join("") || `<p class="small muted">${T("no_suggestions")}</p>`}</div>
    <div class="card"><h3>${T("failing")}</h3>${Object.entries(r.failing_actions).map(([k, v]) => `<div class="small">${esc(k)}: <b>${v}</b></div>`).join("") || `<p class="small muted">—</p>`}</div>
    <div class="card"><h3>${T("load24")}</h3><svg class="spark" viewBox="0 0 300 70" preserveAspectRatio="none">${spark("cpu", "var(--accent)")}${spark("ram", "var(--accent-2)")}</svg>
      <div class="small"><span style="color:var(--accent)">■</span> ${T("cpu")} <span style="color:var(--accent-2)">■</span> ${T("ram")}</div></div></div>`;
};

// ---- settings
pages.settings = async (el) => {
  const [st, backups, plugins] = await Promise.all([api("/settings"), api("/backups"), api("/plugins")]);
  el.innerHTML = `<div class="grid wide">
    <div class="card"><h3>${T("nav_settings")}</h3>
      <label>${T("ui_language")}</label><select id="sl"><option value="ar" ${lang === "ar" ? "selected" : ""}>العربية</option><option value="en" ${lang === "en" ? "selected" : ""}>English</option></select>
      <label>${T("theme")}</label><select id="sth"><option value="dark" ${theme === "dark" ? "selected" : ""}>${T("dark")}</option><option value="light" ${theme === "light" ? "selected" : ""}>${T("light")}</option></select>
      <label>${T("parallel")}</label><input type="number" id="par" min="1" max="10" value="${st.max_parallel_tasks}">
      <label>${T("retries")}</label><input type="number" id="ret" min="0" max="5" value="${st.step_retries}">
      <p><label class="switch"><input type="checkbox" id="low" ${st.low_resource_mode ? "checked" : ""}> ${T("low_resource")}</label></p>
      <label>${T("workspace")}</label><input type="text" id="wsd" value="${esc(st.workspace_dir)}" style="direction:ltr">
      <label>${T("heartbeat")}</label><input type="url" id="hb" value="${esc(st.heartbeat_url)}" style="direction:ltr">
      <p><button class="btn primary" id="saveS">${T("save")}</button></p></div>
    <div class="card"><h3>${T("backups")}</h3>
      <label>${T("auto_backup")}</label><div class="row"><input type="number" id="abh" min="0" value="${st.auto_backup_hours}"><button class="btn primary shrink" id="bnow">${T("backup_now")}</button></div>
      <div class="list" style="margin-top:10px">${backups.map((b) => `<div class="item"><span class="grow small">${esc(b.name)} <span class="muted">${(b.size / 1048576).toFixed(2)} MB</span></span><button class="btn small shrink" data-restore="${esc(b.name)}">${T("restore")}</button></div>`).join("") || `<div class="empty">—</div>`}</div></div></div>
  <div class="card"><h3>${T("plugins")}</h3><p class="small muted">${T("plugins_hint")}</p><p class="small" style="color:var(--warn)">⚠ ${T("plugin_warn")}</p>
    <div class="list">${plugins.map((p) => `<div class="item"><span class="grow"><b>${esc(p.name)}</b> ${p.version ? `<span class="small muted">v${esc(p.version)}</span>` : ""}
      ${p.loaded ? `<span class="badge b-ok">${T("ag_ready")}</span>` : ""}${p.error ? `<span class="badge b-bad">${esc(p.error)}</span>` : ""}
      <div class="small muted">${esc(p.description)}${p.adds.length ? " · " + T("plugin_adds") + ": " + esc(p.adds.join(", ")) : ""}</div></span>
      <button class="btn small shrink ${p.enabled ? "" : "primary"}" data-plug="${esc(p.name)}" data-on="${p.enabled ? 0 : 1}">${p.enabled ? T("disable") : T("enable")}</button></div>`).join("") || `<div class="empty">${T("no_plugins")}</div>`}</div></div>`;
  $("#sl").onchange = (e) => { lang = e.target.value; store.set("lang", lang); applyLang(); api("/settings", { method: "PUT", body: { ui_language: lang } }); go("settings"); };
  $("#sth").onchange = (e) => { theme = e.target.value; store.set("theme", theme); applyTheme(); };
  $("#saveS").onclick = async () => {
    await api("/settings", { method: "PUT", body: { max_parallel_tasks: Number($("#par").value), step_retries: Number($("#ret").value), low_resource_mode: $("#low").checked,
      workspace_dir: $("#wsd").value, heartbeat_url: $("#hb").value, auto_backup_hours: Number($("#abh").value) } });
    toast(T("saved"));
  };
  $("#bnow").onclick = async () => { await api("/backups", { method: "POST" }); toast(T("saved")); render(); };
  el.querySelectorAll("[data-restore]").forEach((b) => b.onclick = async () => { if (confirm(T("restore_confirm"))) { await api(`/backups/${encodeURIComponent(b.dataset.restore)}/restore`, { method: "POST" }); toast(T("restart_needed")); } });
  el.querySelectorAll("[data-plug]").forEach((b) => b.onclick = async () => {
    if (b.dataset.on === "1" && !confirm(T("plugin_warn"))) return;
    try { await api(`/plugins/${encodeURIComponent(b.dataset.plug)}/${b.dataset.on === "1" ? "enable" : "disable"}`, { method: "POST" }); render(); } catch (e) { fail(e); } });
};

// ------------------------------------------------------------------ global controls
async function emergencyClick() {
  try {
    if (state && state.emergency) await api("/emergency/release", { method: "POST" });
    else { if (!confirm(T("emergency_confirm"))) return; const r = await api("/emergency/stop", { method: "POST" }); toast("🛑 " + r.stopped); }
    await poll(); render();
  } catch (e) { fail(e); }
}

document.addEventListener("DOMContentLoaded", async () => {
  applyLang(); applyTheme();
  $("#langBtn").onclick = () => { lang = lang === "ar" ? "en" : "ar"; store.set("lang", lang); applyLang(); go(current); };
  $("#themeBtn").onclick = () => { theme = theme === "dark" ? "light" : "dark"; store.set("theme", theme); applyTheme(); };
  $("#emergencyBtn").onclick = emergencyClick;
  $("#menuBtn").onclick = () => $("#sidebar").classList.toggle("open");
  $("#drawerClose").onclick = () => $("#drawer").classList.add("hidden");
  $("#drawer").onclick = (e) => { if (e.target.id === "drawer") $("#drawer").classList.add("hidden"); };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer").classList.add("hidden"); });
  try { await api("/me"); start(); } catch { /* showAuth already called on 401 */ }
});
})();
