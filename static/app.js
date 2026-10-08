"use strict";
const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const CAT_LABEL = { doctrine: "Doctrine", application: "Application", repentance: "Repentance",
  discipleship: "Discipleship", gospel: "Gospel" };
let pollTimer = null;
let health = null;

const api = async (path, opts = {}) => {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (r.status === 401) { location.href = "/login.html"; throw new Error(t("Please sign in.")); }
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(ts(typeof msg === "string" ? msg : JSON.stringify(msg)));
  }
  return r.json();
};
const toast = (msg) => {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 2600);
};
const fmt = (s) => {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, sec = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(sec).padStart(2, "0");
};
const parseTime = (v) => {
  v = String(v ?? "").trim().replace(",", ".");
  if (!v || !/^[\d.:]+$/.test(v)) return null;
  return v.split(":").reduce((acc, x) => acc * 60 + Number(x), 0);
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// In-page confirm / prompt. The browser's own confirm() and prompt() are blocked in some browsers (embedded
// web views, or after "prevent this page from creating additional dialogs") and then silently answer "No".
function ask(message, { input = null, ok = t("OK"), danger = false } = {}) {
  return new Promise((resolve) => {
    const dlg = $("#ask-dlg"), field = $("#ask-input"), okBtn = $("#ask-ok");
    $("#ask-msg").textContent = message;
    field.hidden = input === null;
    field.value = input ?? "";
    field.rows = String(input ?? "").includes("\n") ? 7 : 2;
    okBtn.textContent = ok;
    okBtn.classList.toggle("danger", danger);
    $("#ask-cancel").onclick = () => dlg.close("cancel");
    field.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !field.value.includes("\n")) { e.preventDefault(); dlg.close("ok"); } };
    dlg.returnValue = "";
    dlg.onclose = () => {
      const yes = dlg.returnValue === "ok";
      resolve(input === null ? yes : yes ? field.value : null);
    };
    dlg.showModal();
    (input === null ? okBtn : field).focus();
  });
}
const askConfirm = (message, opts) => ask(message, opts);           // → true / false
const askText = (message, value = "", opts) => ask(message, { ...opts, input: value });  // → text / null

const media = (pid, name, v) => `/media/${pid}/${name}${v ? `?v=${v}` : ""}`;

// ---------------- options (remembered per browser) ----------------
const OPT_KEY = "fip.options";
function loadOptions() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(OPT_KEY) || "{}"); } catch {}
  if (saved.brand === "FastImpact Preach") delete saved.brand; // old default → new default
  if (saved.min_duration === 35 && saved.max_duration === 75) { delete saved.min_duration; delete saved.max_duration; } // v1 defaults → 30–60
  NOT_REMEMBERED.forEach((k) => delete saved[k]);
  if (!(saved.montage_max >= 30)) { delete saved.montage_max; delete saved.montage; } // saved before montage mode
  if (!("caption_style" in saved)) delete saved.emojis; // saved before the EEIG style: use its default (no emojis)
  return { ...(health?.defaults || {}), ...saved };
}
// Number of clips and the 1-clip range always start from the defaults (10 clips); other settings are remembered.
const NOT_REMEMBERED = ["num_clips", "single_mode", "single_start", "single_end"];
function saveOptions(o) {
  const keep = { ...o };
  NOT_REMEMBERED.forEach((k) => delete keep[k]);
  try { localStorage.setItem(OPT_KEY, JSON.stringify(keep)); } catch {}
}
function readForm(root) {
  const o = {};
  root.querySelectorAll("[name]").forEach((el) => {
    if (el.type === "radio") { if (el.checked) o[el.name] = el.value; return; }
    o[el.name] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
  });
  if ("num_clips" in o) o.num_clips = Math.max(1, Math.min(10, Math.round(o.num_clips) || 1));
  return o;
}
function settingsSummary(o) {
  const lang = o.language === "auto" ? t("auto language") : o.language;
  if (o.num_clips === 1) {
    const m = { ask: t("ask for the part"), auto: t("Claude picks the moment"),
      manual: `${o.single_start || "?"} → ${o.single_end || "?"}` }[o.single_mode] || "";
    return t("1 clip · {m} · {lang}", { m, lang });
  }
  return t("{n} clips · {min}–{max}s{montage} · {lang}", { n: o.num_clips, min: o.min_duration, max: o.max_duration, lang,
    montage: o.montage ? t(" · montage ≤ {max}s", { max: o.montage_max }) : "" });
}

// ---------------- router ----------------
window.addEventListener("hashchange", route);
async function route() {
  clearTimeout(pollTimer);
  const h = location.hash.replace(/^#\/?/, "");
  const [page, id] = h.split("/");
  $("#pub-bar").hidden = true;
  $("#pub-progress").hidden = true;
  document.body.classList.remove("has-pubprog");
  if (page.startsWith("social")) return socialReturn(new URLSearchParams(page.split("?")[1] || ""));
  if (page === "p" && id) return showProject(id);
  if (page === "projects") return showProjects();
  return showHome();
}

// "New Project": the current project is already saved on the server (every step and setting is written
// to its project.json; renders and uploads keep running in the queue) — close any open dialog and go to upload.
function newProject() {
  document.querySelectorAll("dialog[open]").forEach((d) => d.close("cancel"));
  const [page, id] = location.hash.replace(/^#\/?/, "").split("/");
  if (page === "p" && id) {
    const busy = currentProject?.status === "running" || currentProject?.status === "queued";
    toast(busy ? t("Project saved — processing continues in the background (My projects)") : t("Project saved in My projects"));
  }
  if (location.hash === "#/" || location.hash === "") route(); else location.hash = "#/";
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ---------------- home ----------------
function showHome() {
  $("#crumb-page").textContent = t("Upload");
  view.replaceChildren($("#tpl-home").content.cloneNode(true));
  const opts = loadOptions();
  const settings = $("#settings");
  opts.num_clips = Math.max(1, Math.min(10, Number(opts.num_clips) || 10));
  settings.querySelectorAll("[name]").forEach((el) => {
    if (!(el.name in opts)) return;
    if (el.type === "checkbox") el.checked = !!opts[el.name];
    else if (el.type === "radio") el.checked = el.value === (opts[el.name] || "ask");
    else el.value = opts[el.name];
  });
  const sum = () => {
    const o = readForm(settings);
    $("#settings-sum").textContent = settingsSummary(o);
    $("#single-box").hidden = o.num_clips !== 1;
    $("#hl-wrap").style.display = o.caption_style === "impact" ? "" : "none";
    const dimLen = o.num_clips === 1 && o.single_mode !== "auto";
    settings.querySelectorAll("[name=min_duration],[name=max_duration]")
      .forEach((el) => (el.closest("label").style.opacity = dimLen ? 0.45 : 1));
    settings.querySelector("[name=montage_max]").closest("label").style.opacity = o.montage && !dimLen ? 1 : 0.45;
    saveOptions(o);
  };
  settings.addEventListener("input", sum); sum();
  setupLogo();
  setupLibrary();
  settings.querySelector("[name=num_clips]").addEventListener("change", (e) => {
    const v = Math.max(1, Math.min(10, Math.round(+e.target.value) || 1));
    if (+e.target.value !== v) { e.target.value = v; toast(t("Number of clips: 1 to 10")); }
    if (v === 1) settings.open = true;
    sum();
  });
  settings.querySelectorAll("[name=single_start],[name=single_end]").forEach((el) =>
    el.addEventListener("focus", () => {
      settings.querySelector("[name=single_mode][value=manual]").checked = true; sum();
    }));
  const okToStart = () => {
    const o = readForm(settings);
    if (o.num_clips !== 1 || o.single_mode !== "manual") return true;
    const a = parseTime(o.single_start), b = parseTime(o.single_end);
    if (a == null || b == null || b <= a) {
      settings.open = true;
      toast(t("Enter a valid start and end time (e.g. 12:30 → 13:45)"));
      return false;
    }
    return true;
  };
  $("#url-form").addEventListener("submit", (e) => { if (!okToStart()) { e.preventDefault(); e.stopImmediatePropagation(); } }, true);

  $("#url-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = $("#url-input").value.trim();
    if (!url) return $("#url-input").focus();
    try {
      const p = await api("/api/projects", { method: "POST", body: JSON.stringify({ kind: "url", url, options: readForm(settings) }) });
      location.hash = `#/p/${p.id}`;
    } catch (err) { toast(err.message); }
  });

  const dz = $("#dropzone"), fi = $("#file-input");
  fi.addEventListener("change", () => fi.files[0] && okToStart() && startUpload(fi.files[0], readForm(settings)));
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f && okToStart()) startUpload(f, readForm(settings)); });

  if (health?.server_mode) $("#local-btn").hidden = true; // no access to server files online
  $("#local-btn").addEventListener("click", () => {
    if (!okToStart()) return;
    const dlg = $("#local-dlg");
    dlg.returnValue = "";
    dlg.showModal();
    dlg.onclose = async () => {
      if (dlg.returnValue !== "ok") return;
      const path = $("#local-path").value.trim();
      if (!path) return;
      try {
        const p = await api("/api/projects", { method: "POST", body: JSON.stringify({ kind: "local", path, options: readForm(settings) }) });
        location.hash = `#/p/${p.id}`;
      } catch (err) { toast(err.message); }
    };
  });
}

function setupLibrary() {
  const list = $("#lib-list"), info = $("#lib-info");
  const kw = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const refresh = async () => {
    let lib;
    try { lib = await api("/api/broll/library"); } catch { return; }
    info.textContent = t("Your church's own clips, used first when their keywords match.") + " "
      + (lib.stock ? t("Otherwise: Pixabay.") : t("No Pixabay key yet (API key button)."));
    list.innerHTML = lib.items.map((i) => `
      <div class="lib-item" data-id="${i.id}"><img src="${i.thumb}" alt="">
        <div><input data-name value="${esc(i.name)}" title="${t("Name")}"><input data-kw value="${esc(i.keywords.join(", "))}" placeholder="${t("keywords, comma-separated")}" title="${t("Keywords")}"></div>
        <button type="button" class="x" data-del title="${t("Remove from the library")}">×</button></div>`).join("");
  };
  list.onchange = async (e) => {
    const row = e.target.closest(".lib-item"); if (!row) return;
    const body = e.target.dataset.kw != null ? { keywords: kw(e.target.value) } : { name: e.target.value };
    try { await api(`/api/broll/library/${row.dataset.id}`, { method: "PATCH", body: JSON.stringify(body) }); toast(t("Saved")); }
    catch (err) { toast(err.message); }
  };
  list.onclick = async (e) => {
    const row = e.target.closest(".lib-item");
    if (!row || e.target.dataset.del == null) return;
    if (!(await askConfirm(t("Remove this clip from the B-roll library?"), { ok: t("Remove"), danger: true }))) return;
    await api(`/api/broll/library/${row.dataset.id}`, { method: "DELETE" });
    refresh();
  };
  $("#lib-file").onchange = async (e) => {
    const f = e.target.files[0]; e.target.value = "";
    if (!f) return;
    const words = await askText(t("Keywords for “{name}” (comma-separated, French and English), e.g. moisson, blé, harvest, wheat", { name: f.name }), "", { ok: t("Add") });
    if (words === null) return;
    info.textContent = t("Uploading {name}…", { name: f.name });
    const q = new URLSearchParams({ filename: f.name, name: f.name.replace(/\.[^.]+$/, ""), keywords: words });
    const r = await fetch(`/api/broll/library?${q}`, { method: "PUT", body: f });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { toast(ts(j.detail) || t("Upload failed")); }
    else toast(t("Added to the B-roll library: {name}", { name: j.name }));
    refresh();
  };
  refresh();
}

function setupLogo() {
  const img = $("#logo-prev"), info = $("#logo-info");
  const refresh = () => {
    img.onload = () => { img.hidden = false; };
    img.onerror = () => { img.hidden = true; info.textContent = t("No logo: the end card shows the church name only."); };
    img.src = `/api/brand/logo?t=${Date.now()}`;
  };
  $("#logo-file").onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const r = await fetch("/api/brand/logo", { method: "PUT", body: f });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return toast(ts(j.detail) || t("Upload failed"));
    info.textContent = t("New logo saved ({w}×{h} px). Re-render shorts to apply it.", { w: j.size[0], h: j.size[1] });
    refresh();
  };
  $("#logo-reset").onclick = async () => {
    if (!(await askConfirm(t("Go back to the default EEIG logo?"), { ok: t("Reset") }))) return;
    await api("/api/brand/logo", { method: "DELETE" });
    info.textContent = t("Default logo restored.");
    refresh();
  };
  refresh();
}

async function startUpload(file, options) {
  const title = file.name.replace(/\.[^.]+$/, "");
  let p;
  try {
    p = await api("/api/projects", { method: "POST", body: JSON.stringify({ kind: "upload", title, options }) });
  } catch (err) { return toast(err.message); }
  history.replaceState(null, "", `#/p/${p.id}`);
  renderProcess(p);
  const thumb = $(".fc-thumb");
  if (file.type.startsWith("video/")) grabThumb(file).then((src) => src && (thumb.src = src));
  const xhr = new XMLHttpRequest();
  xhr.open("PUT", `/api/projects/${p.id}/upload?filename=${encodeURIComponent(file.name)}`);
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.floor((e.loaded / e.total) * 100);
    $(".bar i").style.width = pct + "%";
    $(".fc-status").textContent = t("Uploading...{pct}%", { pct });
    const li = $('.steps li[data-k="upload"] .lbl');
    if (li) li.textContent = t("Uploading...{pct}%", { pct });
  };
  xhr.onload = () => {
    if (xhr.status === 401) { location.href = "/login.html"; return; }
    if (xhr.status >= 300) { toast(t("Upload failed")); return; }
    showProject(p.id);
  };
  xhr.onerror = () => toast(t("Upload failed — is the server running?"));
  window.__uploadXhr = xhr;
  xhr.send(file);
}

function grabThumb(file) {
  return new Promise((res) => {
    const v = document.createElement("video");
    v.muted = true; v.preload = "metadata"; v.src = URL.createObjectURL(file);
    v.onloadedmetadata = () => { v.currentTime = Math.min(30, v.duration / 4 || 1); };
    v.onseeked = () => {
      const c = document.createElement("canvas"); c.width = 100; c.height = 100;
      const s = Math.min(v.videoWidth, v.videoHeight);
      c.getContext("2d").drawImage(v, (v.videoWidth - s) / 2, (v.videoHeight - s) / 2, s, s, 0, 0, 100, 100);
      res(c.toDataURL("image/jpeg", .8)); URL.revokeObjectURL(v.src);
    };
    v.onerror = () => res(null);
  });
}

// ---------------- processing ----------------
function renderProcess(p) {
  $("#crumb-page").textContent = t("Upload");
  if (!$(".process")) {
    view.replaceChildren($("#tpl-process").content.cloneNode(true));
    $("#cancel-btn").addEventListener("click", async () => {
      if (!(await askConfirm(t("Cancel processing of this video?"), { ok: t("Stop processing"), danger: true }))) return;
      window.__uploadXhr?.abort();
      await api(`/api/projects/${p.id}/cancel`, { method: "POST" }).catch(() => {});
      location.hash = "#/";
    });
  }
  $(".fc-title").textContent = p.title || t("Untitled");
  if (p.thumb) $(".fc-thumb").src = media(p.id, p.thumb);
  if (p.media) {
    const r = $(".fc-res"), d = $(".fc-dur");
    if (p.media.height) { r.textContent = `${Math.min(p.media.height, p.media.width)}p`; r.hidden = false; }
    d.textContent = fmt(p.media.duration); d.hidden = false;
  }
  const order = ["upload", "create", "process", "find", "edit", "finalize"];
  const cur = p.steps[p.step] ? p.step : "upload";
  const curStep = p.steps[cur];
  const doneCount = order.filter((k) => p.steps[k].state === "done").length;
  const overall = Math.min(1, (doneCount + (curStep.state === "active" ? curStep.progress || 0 : 0)) / order.length);
  if (p.status !== "uploading") {
    $(".bar i").style.width = Math.round(overall * 100) + "%";
    $(".fc-status").textContent = p.status === "awaiting_input" ? t("Waiting for your choice") : p.status === "error" ? t("Something went wrong")
      : p.status === "queued" ? t("Waiting in queue…") : `${ts(curStep.label)}… ${Math.round(overall * 100)}%`;
  }
  $(".ph-title").textContent = p.status === "uploading" ? t("Uploading") : p.status === "error" ? t("Processing stopped")
    : p.status === "awaiting_input" ? t("Your turn") : t("Processing");
  if (p.status === "awaiting_input") $(".ph-sub").textContent = t("The sermon is transcribed. Tell us which part to turn into your short.");
  else if (p.status === "error") $(".ph-sub").textContent = t("Fix the problem below, then click Retry — finished steps are kept.");
  else if (p.status !== "uploading") $(".ph-sub").textContent = t("Claude is watching your sermon. You can close this tab — processing continues on your computer.");

  $(".steps").innerHTML = order.map((k) => {
    const s = p.steps[k];
    let label = ts(s.label);
    if (k === "upload" && s.state === "active") label = ts(s.message) || t("Uploading…");
    const pct = s.state === "active" && s.progress > 0.005 && k !== "upload" ? ` · ${Math.round(s.progress * 100)}%` : "";
    const msg = s.state !== "pending" && s.message && k !== "upload" ? `<span class="msg">${esc(ts(s.message))}${pct}</span>` : "";
    return `<li class="${s.state}" data-k="${k}"><span class="dot"></span><span><span class="lbl">${esc(label)}</span>${msg}</span></li>`;
  }).join("");

  renderAsk(p);
  const err = $(".err");
  if (p.status === "error") {
    err.hidden = false;
    err.innerHTML = `${esc(ts(p.error))}<br><button class="btn-primary sm" id="retry">${t("Retry")}</button>`;
    $("#retry").onclick = async () => { await api(`/api/projects/${p.id}/retry`, { method: "POST" }); showProject(p.id); };
  } else err.hidden = true;
}

function renderAsk(p) {
  const box = $("#ask");
  box.hidden = p.status !== "awaiting_input";
  if (box.hidden || box.dataset.id === p.id) return;
  box.dataset.id = p.id;
  const dur = p.media?.duration || 0;
  $(".ask-dur", box).textContent = fmt(dur);
  const player = $(".ask-player", box);
  player.src = `/api/projects/${p.id}/source`;
  const st = $(".ask-start", box), en = $(".ask-end", box), info = $(".ask-info", box);
  st.value = p.options.single_start || "";
  en.value = p.options.single_end || "";
  const check = () => {
    const a = parseTime(st.value), b = parseTime(en.value);
    if (a == null || b == null) { info.textContent = ""; return false; }
    if (b <= a) { info.textContent = t("The end must be after the start."); return false; }
    if (b > dur + 1) { info.textContent = t("The video ends at {t}.", { t: fmt(dur) }); return false; }
    const len = b - a;
    info.textContent = t("Clip length: {t}", { t: fmt(len) }) + (len > 180 ? t(" — long for a short (most are under 90 s)") : "");
    return true;
  };
  [st, en].forEach((el) => el.addEventListener("input", check));
  $(".ask-set-start", box).onclick = () => { st.value = fmt(player.currentTime); check(); };
  $(".ask-set-end", box).onclick = () => { en.value = fmt(player.currentTime); check(); };
  const send = async (body) => {
    try {
      await api(`/api/projects/${p.id}/range`, { method: "POST", body: JSON.stringify(body) });
      box.dataset.id = "";
      player.pause();
      showProject(p.id);
    } catch (e) { toast(e.message); }
  };
  $(".ask-auto", box).onclick = () => send({ mode: "auto" });
  $(".ask-manual", box).onclick = () => {
    if (!check()) { info.textContent ||= t("Enter a start and end time, e.g. 12:30 and 13:45."); return; }
    send({ mode: "manual", start: st.value.trim(), end: en.value.trim() });
  };
  check();
}

async function showProject(id) {
  clearTimeout(pollTimer);
  let p;
  try { p = await api(`/api/projects/${id}`); } catch (e) { toast(e.message); location.hash = "#/"; return; }
  if (location.hash !== `#/p/${id}`) return;
  if (p.status === "done" || (p.clips?.length && p.steps.finalize.state !== "pending" && p.status !== "error")) {
    renderResults(p);
  } else if (p.status === "uploading" && !window.__uploadXhr) {
    renderProcess(p);
  } else {
    renderProcess(p);
  }
  const busy = ["queued", "running", "uploading"].includes(p.status) || p.clips?.some((c) => c.status === "rendering")
    || p.clips?.some((c) => Object.values(c.publish || {}).some((s) => ["queued", "uploading", "processing"].includes(s.status)));
  if (busy) pollTimer = setTimeout(() => showProject(id), 1500);
}

// ---------------- results ----------------
let activeFilter = "all";
function renderResults(p) {
  $("#crumb-page").textContent = t("Clips");
  currentProject = p;
  if (pubProject !== p.id) { pubSel = new Set(); pubProject = p.id; }
  if (social && !social.selected && !sessionGet("fip.socialAsked")) {  // prompt once for the network
    sessionSet("fip.socialAsked", "1");
    setTimeout(() => openSocial(), 300);
  }
  if (!$(".results") || $(".results").dataset.id !== p.id) {
    view.replaceChildren($("#tpl-results").content.cloneNode(true));
    $(".results").dataset.id = p.id;
    activeFilter = "all";
    $("#prep-n").value = 10;
    $("#prep-go").onclick = async () => {
      const n = Math.max(1, Math.min(10, Math.round(+$("#prep-n").value) || 10));
      $("#prep-n").value = n;
      const cur = $(".results").dataset.count;
      if (+cur && !(await askConfirm(t("Prepare {n} new short{s} from this sermon? The current shorts ({cur}) will be replaced.",
        { n, cur, s: n > 1 ? "s" : "", x: n > 1 ? "x" : "" }), { ok: t("Prepare shorts") }))) return;
      try {
        await api(`/api/projects/${p.id}/prepare`, { method: "POST", body: JSON.stringify({ num_clips: n }) });
        showProject(p.id);
      } catch (e) { toast(e.message); }
    };
  }
  $(".results").dataset.count = p.clips.length;
  const dl = $("#dl-json");
  dl.hidden = !p.clips.some((c) => c.status === "done");
  dl.href = media(p.id, "clips.json") + "?download=1";
  const a = p.analysis || {};
  $(".res-title").textContent = a.sermon_title || p.title;
  $(".res-summary").textContent = a.sermon_summary || "";
  $(".res-scriptures").innerHTML = (a.main_scriptures || []).map((s) => `<span class="ref">📖 ${esc(s)}</span>`).join("");
  const busy = ["running", "queued"].includes(p.status);
  $("#prep-go").disabled = busy;
  $("#prep-go").textContent = busy ? t("Preparing {done}/{n}…", { done: p.clips.filter((c) => c.status === "done").length, n: p.clips.length })
    : t("Prepare shorts");

  const cats = [...new Set(p.clips.map((c) => c.category))];
  $("#filters").innerHTML = [["all", t("All clips ({n})", { n: p.clips.length })], ...cats.map((c) => [c, CAT_LABEL[c] ? t(CAT_LABEL[c]) : c])]
    .map(([k, l]) => `<button data-f="${k}" class="${k === activeFilter ? "on" : ""}">${esc(l)}</button>`).join("");
  $("#filters").onclick = (e) => { const b = e.target.closest("button"); if (b) { activeFilter = b.dataset.f; renderResults(p); } };

  const wrap = $(".clips");
  const keep = new Map([...wrap.children].map((el) => [el.dataset.key, el]));
  const list = p.clips.filter((c) => activeFilter === "all" || c.category === activeFilter);
  const nodes = list.map((c) => {
    const pubKey = Object.entries(c.publish || {}).map(([n, s]) => `${n}${s.status}${Math.round((s.progress || 0) * 20)}`).join(",");
    const key = `${c.index}:${c.version || 0}:${c.status}:${pubKey}`;
    if (keep.has(key)) return keep.get(key);
    return clipCard(p, c, key);
  });
  wrap.replaceChildren(...nodes);
  updatePubBar();
  updatePubProgress(p);
}

function clipCard(p, c, key) {
  const el = $("#tpl-clip").content.firstElementChild.cloneNode(true);
  el.dataset.key = key;
  const v = $("video", el);
  if (c.status === "done") {
    v.src = media(p.id, "clips/" + c.video, c.version);
    v.poster = media(p.id, "clips/" + c.thumb, c.version);
    v.preload = "metadata";
  }
  $(".busy", el).hidden = !(c.status === "rendering" || c.status === "pending");
  if (c.status === "pending") $(".busy span", el).textContent = t("Waiting…");
  const sc = $(".score", el);
  sc.textContent = c.score ?? "–";
  sc.className = "score " + (c.score >= 85 ? "hi" : c.score >= 70 ? "mid" : "");
  $(".rank", el).textContent = `#${c.index + 1}`;
  const cat = $(".cat", el);
  cat.textContent = CAT_LABEL[c.category] ? t(CAT_LABEL[c.category]) : c.category;
  cat.classList.add(c.category);
  const st = c.start ?? null;
  const nPass = (c.passages || []).length;
  const tm = $(".time", el);
  if (nPass > 1) {
    const segs = c.segments?.length > 1 ? c.segments : null;
    tm.textContent = t("{n} passages · {d}s", { n: nPass, d: Math.round(c.duration || c.approx_duration || 0) });
    tm.title = segs ? t("Montage of: {list}", { list: segs.map(([s, e]) => `${fmt(s)}–${fmt(e)}`).join(", ") }) : t("Montage");
    tm.insertAdjacentHTML("beforeend", ` <span class="montage-tag">${t("Montage")}</span>`);
  } else {
    tm.textContent = st != null ? `${fmt(c.start)} → ${fmt(c.end)} · ${Math.round(c.duration)}s` : `~${Math.round(c.approx_duration || 0)}s`;
  }
  $(".clip-title", el).textContent = c.title;
  const hook = (c.hook_title ?? c.title) || "";
  $(".hook-line", el).hidden = !hook || p.options?.title_mode === "off";
  $(".hook-text", el).textContent = hook;
  $(".core", el).textContent = c.core_message || "";
  const used = c.broll_used || [];
  $(".broll-line", el).hidden = !used.length;
  $(".broll-line", el).textContent = used.length ? t("B-roll: {list}", { list: used.map((b) => b.credit || b.source).join(" · ") }) : "";
  $(".refs", el).innerHTML = (c.scripture_refs || []).map((r) => `<span class="ref">${esc(r)}</span>`).join("");
  $(".why-text", el).textContent = c.why_meaningful || "";
  $(".sc", el).textContent = [c.self_contained_check, c.refine_note].filter(Boolean).join(" — ");
  $(".transcript", el).textContent = c.text || "";
  if (c.status === "error") {
    const e = document.createElement("p"); e.className = "error"; e.textContent = t("Render failed: {err}", { err: ts((c.error || "").slice(-200)) });
    $(".clip-body", el).append(e);
  }
  const dl = $(".dl", el);
  const ready = c.status === "done";
  if (ready) dl.href = media(p.id, "clips/" + c.video) + "?download=1";
  else dl.removeAttribute("href");
  dl.classList.toggle("disabled", !ready);
  dl.setAttribute("aria-disabled", String(!ready));
  dl.title = ready ? t("Download this short (MP4)") : c.status === "error" ? t("Rendering failed") : t("Available when the short is ready");
  $(".copy", el).onclick = async () => {
    const tags = (c.hashtags || []).map((h) => (h.startsWith("#") ? h : "#" + h)).join(" ");
    const txt = `${c.title}\n\n${c.post_caption || ""}\n\n${(c.scripture_refs || []).join(" · ")}\n\n${tags}`.trim();
    try { await navigator.clipboard.writeText(txt); toast(t("Post text copied")); } catch { askText(t("Copy:"), txt, { ok: t("Close") }); }
  };
  $(".adjust", el).onclick = () => openAdjust(p, c);
  const cb = $(".pub-cb", el), lab = $(".pub", el), canPub = c.status === "done";
  cb.disabled = !canPub;
  lab.classList.toggle("off", !canPub);
  lab.title = canPub ? t("Select this short for publication") : t("Available when the short is ready");
  cb.checked = canPub && pubSel.has(c.index);
  cb.onchange = () => { cb.checked ? pubSel.add(c.index) : pubSel.delete(c.index); updatePubBar(); };
  $(".pub-status", el).innerHTML = Object.entries(c.publish || {}).map(([n, st]) => {
    const L = esc(social?.networks?.[n]?.label || n);
    if (st.status === "done") {
      const where = st.account ? ` · ${esc(st.account)}` : "";
      const what = st.state === "online" ? t("Online on {net}{where}", { net: L, where }) : t("Sent to {net}{where} (processing)", { net: L, where });
      const priv = st.privacy && st.privacy !== "public" ? ` · ${esc(st.privacy)}` : "";
      const label = /^https?:/.test(st.url || "") ? `<a href="${esc(st.url)}" target="_blank" rel="noopener">✓ ${what}${priv} ↗</a>`
        : `<span>✓ ${what}${priv}</span>`;
      return label + (st.note ? `<span class="note">${esc(ts(st.note))}</span>` : "");
    }
    if (st.status === "error") return `<span class="err-t">✗ ${t("{net} failed", { net: L })}</span>`
      + (st.error ? `<span class="note err-note">${esc(ts(st.error))}</span>` : "");
    if (st.status === "uploading") {
      const pct = Math.round((st.progress || 0) * 100);
      return `<span class="muted">${t("Uploading to {net}… {pct}%", { net: L, pct })}<span class="mini"><i style="width:${pct}%"></i></span></span>`;
    }
    if (st.status === "processing") return `<span class="muted">${t("{net} is processing…", { net: L })}</span>`;
    return `<span class="muted">${t("Queued for {net}…", { net: L })}</span>`;
  }).join("<br>");
  return el;
}

// ---------------- adjust boundaries ----------------
async function openAdjust(p, c) {
  if (p.status === "running") return toast(t("Please wait until processing finishes."));
  // passages in play order (a montage has several; a single clip has one)
  let ps = (c.passages?.length ? c.passages : [{ start_sentence: c.start_sentence, end_sentence: c.end_sentence, role: "body" }])
    .map((x) => ({ a: x.start_sentence, b: x.end_sentence, role: x.role || "body" }));
  const lo = Math.max(0, Math.min(...ps.map((x) => x.a)) - 8), hi = Math.max(...ps.map((x) => x.b)) + 8;
  let sents;
  try { sents = await api(`/api/projects/${p.id}/sentences?lo=${lo}&hi=${hi}`); } catch (e) { return toast(e.message); }
  const byId = Object.fromEntries(sents.map((s) => [s.id, s]));
  let cur = 0;
  const dlg = $("#adjust-dlg"), list = $("#sent-list"), chips = $("#adj-passages");
  const span = (x) => (byId[x.b] && byId[x.a] ? byId[x.b].e - byId[x.a].s : 0);
  const overlaps = () => ps.some((x, i) => ps.some((y, j) => j > i && !(x.b < y.a || x.a > y.b)));
  const drawChips = () => {
    chips.hidden = ps.length < 2;
    $("#adj-help").textContent = ps.length < 2
      ? t("Pick the first and last sentence. Each clip should open the thought and land the message.")
      : t("This short is a montage: its passages play in this order. Click a passage to edit its first and last sentence; × removes it.");
    chips.innerHTML = ps.map((x, i) => `
      <span class="pp ${i === cur ? "on" : ""}" data-i="${i}">${i + 1}${x.role === "hook" ? ` <span class="role">${t("hook")}</span>` : ""}
        ${byId[x.a] ? fmt(byId[x.a].s) : "?"}–${byId[x.b] ? fmt(byId[x.b].e) : "?"}
        <button type="button" class="x" data-del="${i}" title="${t("Remove this passage")}">×</button></span>`).join("");
  };
  const draw = (scroll) => {
    const x = ps[cur];
    const win = sents.filter((s) => s.id >= x.a - 8 && s.id <= x.b + 8);
    list.innerHTML = win.map((s) => {
      const other = ps.some((y, i) => i !== cur && s.id >= y.a && s.id <= y.b);
      return `
      <div class="sent ${s.id >= x.a && s.id <= x.b ? "in" : other ? "other" : ""}">
        <span class="ts">${fmt(s.s)}</span><span>${esc(s.text)}</span>
        <span class="pick"><button type="button" data-a="${s.id}" class="${s.id === x.a ? "on" : ""}">${t("Start")}</button><button type="button" data-b="${s.id}" class="${s.id === x.b ? "on" : ""}">${t("End")}</button><button type="button" class="br" data-br="${s.id}" title="${t("Add a B-roll shot on this sentence")}">${t("B-roll")}</button></span>
      </div>`;
    }).join("");
    drawChips();
    const total = ps.reduce((t, y) => t + span(y), 0);
    const bad = ps.some((y) => y.b < y.a) || overlaps();
    $("#adj-dur").textContent = (ps.length > 1 ? t("Montage · {n} passages · ", { n: ps.length }) : "") + `${Math.round(total)} s`
      + (overlaps() ? t(" · passages overlap") : "");
    $("#adj-ok").disabled = bad;
    if (scroll) list.querySelector(".sent.in")?.scrollIntoView({ block: "center" });
  };
  // B-roll cues: review, change the search, pick a library clip, another result, remove, add
  let cues = (c.broll || []).map((b) => ({ ...b }));
  const lib = await api("/api/broll/library").catch(() => ({ items: [], stock: false }));
  const brBox = $("#adj-broll");
  const drawBroll = () => {
    const libOpts = lib.items.map((i) => `<option value="library:${i.id}">${t("Library: {name}", { name: esc(i.name) })}</option>`).join("");
    brBox.innerHTML = cues.length ? cues.map((b, i) => {
      const s = byId[b.sentence];
      const where = s ? `${fmt(s.s)} · “${esc(b.word || s.text.split(" ")[0])}”` : `S${b.sentence}`;
      const thumb = b.thumb ? `<img src="${esc(b.thumb)}" alt="">` : `<img alt="">`;
      const state = b.file ? esc(b.credit || b.source || "") : `<span class="be-miss">${t("found at next render")}</span>`;
      return `<div class="be-row" data-i="${i}">${thumb}
        <div class="be-q"><span class="be-at">${where} · ${b.seconds || 3}s · ${state}</span>
          <input data-q="${i}" value="${esc(b.query || "")}" placeholder="${t("search (English), e.g. wheat field")}"></div>
        <select data-src="${i}"><option value="auto">${t("Auto")}</option><option value="pixabay">Pixabay</option>${libOpts}</select>
        <button type="button" class="btn-outline sm" data-next="${i}" title="${t("Use another search result")}">${t("Other")}</button>
        <button type="button" class="x" data-rm="${i}" title="${t("Remove this B-roll")}">×</button></div>`;
    }).join("") : `<p class="muted">${t("No B-roll in this short.")}</p>`;
    cues.forEach((b, i) => { const sel = brBox.querySelector(`[data-src="${i}"]`); sel.value = b.source || "auto"; });
    $("#adj-broll-help").textContent = lib.stock || lib.items.length
      ? t('Cutaway shots over the preacher. Use "B-roll" on a sentence above to add one.')
      : t("Add a Pixabay key (API key button) or clips to the B-roll library to use B-roll.");
  };
  const reset = (b) => { delete b.file; delete b.thumb; delete b.credit; delete b.at; delete b.ref; };
  brBox.onchange = (e) => {
    const t = e.target;
    if (t.dataset.q != null) { const b = cues[+t.dataset.q]; if (b.query !== t.value.trim()) { b.query = t.value.trim(); b.variant = 0; reset(b); } }
    if (t.dataset.src != null) { const b = cues[+t.dataset.src]; b.source = t.value; b.variant = 0; reset(b); }
    drawBroll();
  };
  brBox.onclick = (e) => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.rm != null) cues.splice(+t.dataset.rm, 1);
    if (t.dataset.next != null) { const b = cues[+t.dataset.next]; b.variant = (b.variant || 0) + 1; if ((b.source || "auto") === "auto") b.source = "pixabay"; reset(b); }
    drawBroll();
  };
  list.onclick = (e) => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.dataset.br != null) {
      if (cues.length >= 3) return toast(t("At most 3 B-roll shots per short"));
      const s = byId[+t.dataset.br];
      cues.push({ sentence: +t.dataset.br, word: s ? s.text.split(/\s+/)[0].replace(/[^\p{L}\p{N}'’-]/gu, "") : "", query: "", keywords: [], seconds: 3, source: "auto", variant: 0 });
      drawBroll();
      brBox.querySelector(`[data-q="${cues.length - 1}"]`)?.focus();
      return;
    }
    if (t.dataset.a) ps[cur].a = +t.dataset.a; if (t.dataset.b) ps[cur].b = +t.dataset.b; draw();
  };
  drawBroll();
  chips.onclick = (e) => {
    const del = e.target.closest("[data-del]");
    if (del) { ps.splice(+del.dataset.del, 1); cur = Math.min(cur, ps.length - 1); if (ps.length === 1) ps[0].role = "body"; return draw(true); }
    const chip = e.target.closest(".pp"); if (chip) { cur = +chip.dataset.i; draw(true); }
  };
  draw();
  $("#adj-hook").value = (c.hook_title ?? c.title) || "";
  const slidesNow = ["auto", "crop", "whole"].includes(c.slides) ? c.slides : "auto";
  dlg.querySelectorAll("[name=adj-slides]").forEach((r) => (r.checked = r.value === slidesNow));
  dlg.returnValue = "";
  dlg.showModal();
  list.querySelector(".sent.in")?.scrollIntoView({ block: "center" });
  dlg.onclose = async () => {
    if (dlg.returnValue !== "ok") return;
    const slides = dlg.querySelector("[name=adj-slides]:checked")?.value || "auto";
    try {
      await api(`/api/projects/${p.id}/clips/${c.index}/adjust`, { method: "POST", body: JSON.stringify({
        start_sentence: Math.min(...ps.map((x) => x.a)), end_sentence: Math.max(...ps.map((x) => x.b)), slides,
        passages: ps.map((x) => ({ start_sentence: x.a, end_sentence: x.b, role: x.role })),
        broll: cues.map((b) => ({ sentence: b.sentence, word: b.word || "", query: b.query || "", keywords: b.keywords || [],
          seconds: Math.min(4.5, Math.max(1.5, +b.seconds || 3)), source: b.source || "auto", variant: b.variant || 0 })),
        hook_title: $("#adj-hook").value.trim() }) });
      toast(t("Re-rendering clip…"));
      showProject(p.id);
    } catch (e) { toast(e.message); }
  };
}

// ---------------- social publishing ----------------
const NETS = ["youtube", "instagram", "facebook", "tiktok"];
const NET_BADGE = { youtube: "▶", instagram: "◎", facebook: "f", tiktok: "♪" };
let social = null, pubSel = new Set(), pubProject = null, currentProject = null;
const sessionGet = (k) => { try { return sessionStorage.getItem(k); } catch { return null; } };
const sessionSet = (k, v) => { try { sessionStorage.setItem(k, v); } catch {} };

async function loadSocial() {
  try { social = await api("/api/social"); } catch { social = null; }
  renderSocialBtn();
  updatePubBar();
}

function renderSocialBtn() {
  const b = $("#social-btn"), sel = social?.selected, n = sel && social.networks[sel];
  const online = !!n?.connected;
  b.classList.toggle("online", online);
  $("#social-label").textContent = !n ? t("Publish to…") : online ? t("{label} · online", { label: n.label }) : t("{label} · not connected", { label: n.label });
  b.title = online ? t("Connected to {label}{as}", { label: n.label, as: n.account ? t(" as {name}", { name: n.account }) : "" })
    : t("Choose and connect a social network");
  // Account / channel selector (one Google login can own several YouTube channels)
  const wrap = $("#acct-wrap"), selEl = $("#acct-select");
  wrap.hidden = !online;
  if (!online) return;
  const kind = t(sel === "youtube" ? "channel" : sel === "facebook" ? "Page" : "account");
  selEl.innerHTML = n.accounts.map((a) => `<option value="${esc(a.id)}" ${a.active ? "selected" : ""}>${esc(a.name)}</option>`).join("")
    + `<option value="__add">${t("＋ Add another {kind}…", { kind })}</option>`;
  wrap.title = t("Uploads go to this {kind}", { kind }) + (n.default_account ? t(" (default: {name})", { name: n.default_account }) : "");
  selEl.onchange = async () => {
    if (selEl.value === "__add") {
      sessionSet("fip.returnTo", location.hash);
      location.href = `/api/social/${sel}/connect`;
      return;
    }
    try {
      social = await api(`/api/social/${sel}/account`, { method: "POST", body: JSON.stringify({ id: selEl.value }) });
      renderSocialBtn();
      updatePubBar();
      toast(t("Publishing to {net} as {acct}", { net: social.networks[sel].label, acct: social.networks[sel].account }));
    } catch (e) { toast(e.message); }
  };
}

function openSocial(focus) {
  const dlg = $("#social-dlg"), err = $("#social-err");
  let cur = focus || social?.selected || "youtube", editKeys = false;
  err.hidden = true;
  const showErr = (m) => { err.textContent = m; err.hidden = false; };
  const draw = () => {
    $("#nets").innerHTML = NETS.map((n) => {
      const st = social?.networks?.[n] || {};
      const label = st.connected ? t("● Online") : st.configured ? t("Not connected") : t("Set up needed");
      return `<button type="button" class="net ${n === cur ? "on" : ""}" data-n="${n}">
        <span class="badge b-${n}">${NET_BADGE[n]}</span><span>${esc(st.label || n)}</span>
        <span class="st ${st.connected ? "ok" : ""}">${label}</span></button>`;
    }).join("");
    const st = social?.networks?.[cur] || {};
    const panel = $("#net-panel");
    if (st.connected && !editKeys) {
      const kind = t(cur === "youtube" ? "channel" : cur === "facebook" ? "Page" : "account");
      panel.innerHTML = `<p>✅ ${t("Connected to {label}. Uploads go to the selected {kind}{def}:", { label: `<b>${esc(st.label)}</b>`, kind,
        def: st.default_account ? t(" (default: {name})", { name: `<b>${esc(st.default_account)}</b>` }) : "" })}</p>
        <div class="acct-list">${st.accounts.map((a) => `<div class="acct-row ${a.active ? "on" : ""}">
          <label><input type="radio" name="acct-pick" value="${esc(a.id)}" ${a.active ? "checked" : ""}> ${esc(a.name)}</label>
          <button type="button" class="linkbtn-plain muted" data-act="remove" data-id="${esc(a.id)}">${t("Remove")}</button></div>`).join("")}</div>
        <div class="row"><button type="button" class="btn-outline sm" data-act="connect">${t("＋ Add another {kind}", { kind })}</button>
        <button type="button" class="linkbtn-plain muted" data-act="disconnect">${t("Disconnect all")}</button></div>
        ${cur === "youtube" ? `<p class="muted" style="margin-top:10px">${t("Google asks which channel to use each time you add one. If {eeig} is not offered, its owner must add your Google account as a {mgr} of the channel's Brand Account.", { eeig: "<b>EEIG GVA</b>", mgr: `<b>${t("Manager")}</b>` })}</p>` : ""}`;
    } else if (st.configured && !editKeys) {
      panel.innerHTML = `<p>${t("Sign in to {label} to allow this app to publish your shorts.", { label: `<b>${esc(st.label)}</b>` })}</p>
        <div class="row"><button type="button" class="btn-primary sm" data-act="connect">${t("Connect {label}", { label: esc(st.label) })}</button>
        <button type="button" class="linkbtn-plain muted" data-act="keys">${t("Change app keys ({id})", { id: esc(st.client_id) })}</button></div>`;
    } else {
      const shared = st.app === "META" ? t(" (shared by Instagram and Facebook)") : "";
      panel.innerHTML = `<p><b>${t("One-time setup:")}</b> ${t("{label} only lets registered apps publish. Create your developer app{shared}:", { label: esc(st.label), shared })}</p>
        <p class="muted">${esc(st.setup || "")}</p>
        <p class="muted">${t("Redirect URI to register:")}</p>
        <div class="uri"><code id="redir">${esc(st.redirect_uri || "")}</code><button type="button" class="btn-outline sm" data-act="copy">${t("Copy")}</button></div>
        <label class="fl">${t("Client ID (App ID / Client key)")}<input class="text" id="cid" spellcheck="false"></label>
        <label class="fl">${t("Client secret")}<input class="text" id="csec" type="password" spellcheck="false"></label>
        <div class="row"><button type="button" class="btn-primary sm" data-act="save">${t("Save & connect")}</button></div>`;
    }
  };
  $("#nets").onclick = async (e) => {
    const b = e.target.closest("[data-n]"); if (!b) return;
    cur = b.dataset.n; editKeys = false; err.hidden = true;
    try { social = await api("/api/social/select", { method: "POST", body: JSON.stringify({ network: cur }) }); await loadSocial(); }
    catch (x) { showErr(x.message); }
    draw();
  };
  $("#net-panel").onchange = async (e) => {
    if (e.target.name !== "acct-pick") return;
    try {
      social = await api(`/api/social/${cur}/account`, { method: "POST", body: JSON.stringify({ id: e.target.value }) });
      renderSocialBtn(); updatePubBar(); draw();
    } catch (x) { showErr(x.message); }
  };
  $("#net-panel").onclick = async (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act; if (!act) return;
    err.hidden = true;
    try {
      if (act === "connect") { sessionSet("fip.returnTo", location.hash); location.href = `/api/social/${cur}/connect`; return; }
      if (act === "keys") { editKeys = true; draw(); return; }
      if (act === "copy") { await navigator.clipboard.writeText($("#redir").textContent); toast(t("Redirect URI copied")); return; }
      if (act === "disconnect") {
        if (!(await askConfirm(t("Disconnect every {label} account?", { label: social.networks[cur].label }), { ok: t("Disconnect all"), danger: true }))) return;
        await api(`/api/social/${cur}/disconnect`, { method: "POST" });
      }
      if (act === "remove") {
        const id = e.target.closest("[data-id]").dataset.id;
        const name = social.networks[cur].accounts.find((a) => a.id === id)?.name || id;
        if (!(await askConfirm(t("Remove {name} from FastImpact Preach?", { name }), { ok: t("Remove"), danger: true }))) return;
        await api(`/api/social/${cur}/disconnect`, { method: "POST", body: JSON.stringify({ id }) });
      }
      if (act === "save") {
        const id = $("#cid").value.trim(), sec = $("#csec").value.trim();
        if (!id || !sec) return showErr(t("Paste both the client ID and the client secret."));
        await api(`/api/social/${cur}/app`, { method: "POST", body: JSON.stringify({ client_id: id, client_secret: sec }) });
        sessionSet("fip.returnTo", location.hash);
        location.href = `/api/social/${cur}/connect`;
        return;
      }
      await loadSocial(); editKeys = false; draw();
    } catch (x) { showErr(x.message); }
  };
  if (!social?.selected) {  // default network: YouTube
    api("/api/social/select", { method: "POST", body: JSON.stringify({ network: cur }) }).then(loadSocial).catch(() => {});
  }
  draw();
  dlg.onclose = () => { loadSocial(); if (currentProject && $(".results")) renderResults(currentProject); };
  if (!dlg.open) dlg.showModal();
}

async function socialReturn(q) {
  await loadSocial();
  const back = sessionGet("fip.returnTo") || "#/";
  sessionSet("fip.returnTo", "");
  history.replaceState(null, "", back);
  await route();
  if (q.get("error")) { openSocial(); const e = $("#social-err"); e.textContent = ts(q.get("error")); e.hidden = false; }
  else if (q.get("connected")) toast(t("Connected to {label} ✓", { label: social?.networks?.[q.get("connected")]?.label || t("network") }));
}

function readyClips() {
  return (currentProject?.clips || []).filter((c) => c.status === "done");
}

function updateSelectAll() {
  const btn = $("#pub-all");
  if (!btn) return;
  const ready = readyClips();
  const all = ready.length > 0 && ready.every((c) => pubSel.has(c.index));
  btn.disabled = ready.length === 0;
  btn.classList.toggle("on", all);
  btn.textContent = all ? t("☐ None Video to Publish") : t("☑ Select All videos to Publish");
  btn.title = all ? t("Untick every short") : t("Tick all {n} finished shorts for publication", { n: ready.length });
}

function toggleSelectAll() {
  const ready = readyClips();
  const all = ready.length > 0 && ready.every((c) => pubSel.has(c.index));
  pubSel = all ? new Set() : new Set(ready.map((c) => c.index));
  document.querySelectorAll(".clip").forEach((el) => {
    const cb = $(".pub-cb", el), idx = +el.dataset.key.split(":")[0];
    if (cb && !cb.disabled) cb.checked = pubSel.has(idx);
  });
  updatePubBar();
}

function updatePubBar() {
  updateSelectAll();
  const bar = $("#pub-bar"), n = $(".results") ? pubSel.size : 0;
  bar.hidden = n === 0;
  if (!n) return;
  const sel = social?.selected, net = sel && social.networks[sel], ok = !!net?.connected;
  const why = !net ? t("Choose a social network") : t("Connect {label} first", { label: esc(net.label) });
  $("#pub-bar-text").innerHTML = t("{n} short{s} selected", { n: `<b>${n}</b>`, s: n > 1 ? "s" : "" })
    + (net ? t(" for {net}", { net: `<b>${esc(net.label)}</b>` }) : "") + (ok && net.account ? ` · ${esc(net.account)}` : "")
    + (ok ? "" : ` · <button type="button" class="linkbtn-plain hint" id="pub-fix">${why} ↑</button>`);
  const go = $("#pub-go");
  go.disabled = !ok;
  go.title = ok ? t("Publish {n} short{s} to {net}", { n, s: n > 1 ? "s" : "", net: net.label }) : why;
  const fix = $("#pub-fix");
  if (fix) fix.onclick = () => openSocial();
}

const pubSeen = {};  // batch → was it still running when we last looked (to notify once on completion)
const localGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const localSet = (k, v) => { try { localStorage.setItem(k, v); } catch {} };

function latestBatch(p) {
  let best = null;
  for (const c of p.clips || []) {
    for (const [net, st] of Object.entries(c.publish || {})) {
      if (st.batch && (!best || st.batch > best.batch)) best = { batch: st.batch, net };
    }
  }
  if (!best) return null;
  best.items = (p.clips || []).map((c) => ({ c, st: c.publish?.[best.net] })).filter((x) => x.st?.batch === best.batch);
  return best;
}

function updatePubProgress(p) {
  const box = $("#pub-progress"), b = latestBatch(p);
  const hide = !b || localGet(`fip.pubDismissed.${p.id}`) === String(b.batch);
  box.hidden = hide;
  document.body.classList.toggle("has-pubprog", !hide);
  if (!b) return;
  const L = social?.networks?.[b.net]?.label || b.net;
  const n = b.items.length;
  const weight = ({ st }) => st.status === "done" || st.status === "error" ? 1
    : st.status === "processing" ? 0.95 : st.status === "uploading" ? 0.05 + 0.85 * (st.progress || 0) : 0;
  const pct = Math.round(100 * b.items.reduce((a, x) => a + weight(x), 0) / n);
  const online = b.items.filter((x) => x.st.status === "done" && x.st.state === "online").length;
  const sent = b.items.filter((x) => x.st.status === "done" && x.st.state !== "online").length;
  const failed = b.items.filter((x) => x.st.status === "error").length;
  const finished = b.items.every((x) => ["done", "error"].includes(x.st.status));
  const current = b.items.find((x) => ["uploading", "processing"].includes(x.st.status));
  box.classList.toggle("done", finished && !failed);
  box.classList.toggle("failed", finished && failed > 0);
  $("#pp-fill").style.width = `${finished ? 100 : pct}%`;
  const s = (k) => (k > 1 ? "s" : "");
  let text, sub;
  if (!finished) {
    text = t("Publishing {n} short{s} to {net} · {done} of {n} done · {pct}%", { n, s: s(n), net: `<b>${esc(L)}</b>`, done: online + sent, pct });
    sub = current ? `#${current.c.index + 1} “${esc(current.c.title)}” — ${current.st.status === "processing"
      ? t("{net} is processing the video…", { net: esc(L) }) : t("uploading {pct}%", { pct: Math.round((current.st.progress || 0) * 100) })}` : t("Waiting in queue…");
  } else {
    text = failed && !online && !sent ? t("Publishing to {net} failed", { net: `<b>${esc(L)}</b>` })
      : t("{n} short{s} online on {net}", { n: `<b>${online}</b>`, s: s(online), net: `<b>${esc(L)}</b>` })
        + (sent ? t(" · {n} still processing on {net}", { n: sent, net: esc(L) }) : "")
        + (failed ? ` · <span class="err-t">${t("{n} failed", { n: failed })}</span>` : "");
    const priv = b.items.filter((x) => x.st.note).length;
    const why = b.items.find((x) => x.st.status === "error" && x.st.error)?.st.error;
    sub = (why ? `<span class="err-t">${esc(ts(why))}</span><br>` : "")
      + (priv ? t("{n} kept Private by {net} — see the note on the card. ", { n: priv, net: esc(L) }) : "")
      + (online || sent ? t("Links are on each short's card.") : "");
  }
  $("#pp-text").innerHTML = text;
  $("#pp-sub").innerHTML = sub;
  $("#pp-close").onclick = () => { localSet(`fip.pubDismissed.${p.id}`, String(b.batch)); updatePubProgress(p); };

  // Notify once, when a batch we watched running is now complete.
  if (pubSeen[b.batch] === true && finished) {
    const msg = failed && !online && !sent ? t("Publishing to {net} failed.", { net: L })
      : t("{n} short{s} online on {net}", { n: online, s: s(online), net: L }) + (sent ? t(", {n} still processing", { n: sent }) : "")
        + (failed ? t(", {n} failed", { n: failed }) : "") + ".";
    toast(msg);
    if ("Notification" in window && Notification.permission === "granted") {
      try { new Notification("FastImpact Preach", { body: msg, icon: "/logo.svg" }); } catch {}
    }
  }
  pubSeen[b.batch] = !finished;
}

function setupPublish() {
  $("#social-btn").onclick = () => openSocial();
  document.addEventListener("click", (e) => { if (e.target.closest("#pub-all")) toggleSelectAll(); });
  $("#pub-clear").onclick = () => {
    pubSel.clear();
    document.querySelectorAll(".pub-cb").forEach((c) => (c.checked = false));
    updatePubBar();
  };
  $("#pub-go").onclick = () => {
    const net = social?.networks?.[social?.selected], p = currentProject;
    if (!net?.connected || !p) return;
    const chosen = p.clips.filter((c) => pubSel.has(c.index) && c.status === "done");
    $("#pub-title").textContent = t("Publish {n} short{s} to {net}", { n: chosen.length, s: chosen.length > 1 ? "s" : "", net: net.label })
      + (net.account ? ` · ${net.account}` : "");
    $("#pub-list").innerHTML = chosen.map((c) => `<li>#${c.index + 1} — ${esc(c.title)}</li>`).join("");
    const sel = $("#pub-privacy"), row = $("#pub-privacy-row"), netId = social.selected;
    row.hidden = !["youtube", "tiktok"].includes(netId);
    sel.querySelector('[value="unlisted"]').hidden = netId !== "youtube";
    sel.value = "public";
    $("#pub-note").textContent = t({
      youtube: "Uploaded as YouTube Shorts with the title, caption, Bible references and hashtags.",
      instagram: "Published as Reels with the caption, Bible references and hashtags.",
      facebook: "Published as Reels on your Facebook Page.",
      tiktok: "Until TikTok audits your developer app, TikTok only allows private posts.",
    }[netId]) + t(" Account: {acct}.", { acct: net.account || net.label });
    const dlg = $("#pub-dlg");
    dlg.returnValue = "";
    dlg.showModal();
    dlg.onclose = async () => {
      if (dlg.returnValue !== "ok") return;
      try {
        if ("Notification" in window && Notification.permission === "default") {
          try { await Notification.requestPermission(); } catch {}
        }
        await api(`/api/projects/${p.id}/publish`, { method: "POST", body: JSON.stringify({
          network: netId, clips: chosen.map((c) => c.index), privacy: row.hidden ? "public" : sel.value }) });
        toast(t("Publishing {n} short{s} to {net}… progress is shown below.", { n: chosen.length, s: chosen.length > 1 ? "s" : "", net: net.label }));
        pubSel.clear();
        showProject(p.id);
      } catch (x) { toast(x.message); }
    };
  };
}

// ---------------- projects list ----------------
async function showProjects() {
  $("#crumb-page").textContent = t("Projects");
  const list = await api("/api/projects");
  view.innerHTML = `<section class="plist"><h1>${t("My projects")}</h1>${list.length ? "" : `<p class="muted">${t("No projects yet.")} <a href="#/" style="color:var(--purple)">${t("Upload a sermon")}</a>.</p>`}
    ${list.map((p) => `<a class="prow" href="#/p/${p.id}">
      ${p.thumb ? `<img src="${media(p.id, p.thumb)}" alt="">` : '<img alt="">'}
      <span class="t"><b>${esc(p.title)}</b><span class="muted">${new Date(p.created * 1000).toLocaleString(LANG === "fr" ? "fr-CH" : "en-GB")} · ${p.media ? fmt(p.media.duration) : ""} · ${t("{n} clips", { n: p.clips })}</span></span>
      <span class="status ${p.status}">${t(p.status)}</span>
      <button class="del" data-del="${p.id}" title="${t("Delete project")}">${t("Delete")}</button></a>`).join("")}</section>`;
  view.onclick = async (e) => {
    const d = e.target.closest("[data-del]"); if (!d) return;
    e.preventDefault();
    const title = d.closest(".prow")?.querySelector(".t b")?.textContent || "";
    if (!(await askConfirm(t("Delete this project and its clips? This cannot be undone.") + (title ? `\n\n“${title}”` : ""),
      { ok: t("Delete"), danger: true }))) return;
    await api(`/api/projects/${d.dataset.del}`, { method: "DELETE" });
    showProjects();
  };
}

// ---------------- boot ----------------
function showKeyStatus(k) {
  const b = $("#key-btn");
  b.classList.toggle("ok", !!k.configured);
  $("#key-label").textContent = k.configured ? `Claude · ${k.key_masked}` : t("Add API key");
  b.title = k.configured ? t("Claude API key {key} · model {model}", { key: k.key_masked, model: k.model }) : t("No Claude API key yet — click to add it");
  $("#key-cur").textContent = k.key_masked || t("none");
  $("#ws-cur").textContent = k.workspace_masked || t("none");
  $("#px-cur").textContent = k.pixabay_masked || t("none");
}

function setupKeyDialog() {
  const dlg = $("#key-dlg"), inp = $("#key-new"), ws = $("#ws-new"), px = $("#px-new"), err = $("#key-err");
  const save = async (body) => {
    err.hidden = true;
    try {
      const k = await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
      showKeyStatus(k);
      inp.value = ""; ws.value = ""; px.value = "";
      toast(body.pixabay_key && !body.api_key ? t("Pixabay key saved ({key})", { key: k.pixabay_masked })
        : k.configured ? t("API key saved ({key})", { key: k.key_masked }) : t("API key removed"));
      return true;
    } catch (e) { err.textContent = e.message; err.hidden = false; return false; }
  };
  $("#key-btn").onclick = () => { err.hidden = true; inp.value = ""; ws.value = ""; px.value = ""; dlg.showModal(); inp.focus(); };
  $("#key-paste").onclick = async () => {
    try { inp.value = (await navigator.clipboard.readText()).trim(); }
    catch { toast(t("Clipboard blocked — press Ctrl+V in the field")); inp.focus(); }
  };
  $("#key-save").onclick = async () => {
    const body = {};
    if (inp.value.trim()) body.api_key = inp.value.trim();
    if (ws.value.trim()) body.workspace_id = ws.value.trim();
    if (px.value.trim()) body.pixabay_key = px.value.trim();
    if (!Object.keys(body).length) { dlg.close(); return; }
    if (await save(body)) dlg.close();
  };
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#key-save").click(); } });
  $("#key-remove").onclick = async () => {
    if (await askConfirm(t("Remove the saved API key and workspace ID from this computer?"), { ok: t("Remove key"), danger: true }))
      await save({ api_key: "", workspace_id: "" });
  };
}

(async () => {
  mountLangSwitch($(".top-actions"));
  $("#new-project").onclick = newProject;
  setupKeyDialog();
  setupPublish();
  try {
    health = await api("/api/health");
    showKeyStatus(await api("/api/settings"));
    if (health.auth) {
      $("#logout").hidden = false;
      $("#logout").onclick = async () => { await api("/api/logout", { method: "POST" }); location.href = "/login.html"; };
    }
    await loadSocial();
  } catch {}
  route();
})();
