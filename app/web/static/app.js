// Fosol Sathi front end: no build step, one module.
const FS = window.FS || { lang: "bn", strings: { bn: {}, en: {} } };
const LANG = FS.lang === "en" ? "en" : "bn";
const OTHER = LANG === "en" ? "bn" : "en";
const S = (k, l = LANG) => (FS.strings[l] && FS.strings[l][k]) || (FS.strings.en && FS.strings.en[k]) || k;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const page = document.body.dataset.page || "";
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
};

// ---------- network: slow and offline banners ----------
const slowBanner = $("#net-slow");
const offBanner = $("#net-offline");
function showOffline() {
  const on = navigator.onLine === false;
  offBanner.classList.toggle("show", on);
  const t = store.get("fs-last-advice");
  const d = $("#net-offline-detail");
  if (d) d.textContent = t ? S("net.offline").replace("{time}", t) : "";
  const mic = $("#mic");
  if (mic) mic.disabled = on;
}
window.addEventListener("online", showOffline);
window.addEventListener("offline", showOffline);
showOffline();
const lastCard = $(".card.decision[data-decision] .mono");
if (lastCard) store.set("fs-last-advice", lastCard.textContent.trim());

async function api(url, opts = {}) {
  const timer = setTimeout(() => slowBanner.classList.add("show"), 5000);
  try {
    const r = await fetch(url, { credentials: "same-origin", ...opts });
    const ct = r.headers.get("content-type") || "";
    const body = ct.includes("json") ? await r.json() : await r.text();
    if (!r.ok) throw Object.assign(new Error((body && body.message) || r.statusText), { status: r.status, body });
    return body;
  } finally {
    clearTimeout(timer);
    slowBanner.classList.remove("show");
  }
}

// ---------- language and theme ----------
$("#lang-switch")?.addEventListener("click", async (e) => {
  await api("/api/lang", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lang: e.currentTarget.dataset.to }) });
  location.reload();
});
function applyTheme(th) {
  if (th === "light" || th === "dark") document.documentElement.dataset.theme = th;
  else delete document.documentElement.dataset.theme;
}
const themeSeg = $("#theme-seg");
if (themeSeg) {
  const cur = store.get("fs-theme") || "system";
  const r = $(`input[value="${cur}"]`, themeSeg);
  if (r) r.checked = true;
  themeSeg.addEventListener("change", (e) => { store.set("fs-theme", e.target.value); applyTheme(e.target.value); });
}

// ---------- countdowns ----------
function tickCountdowns() {
  $$(".countdown[data-deadline]").forEach((el) => {
    const ms = new Date(el.dataset.deadline).getTime() - Date.now();
    if (ms <= 0) { el.textContent = el.dataset.closedText; el.classList.add("closed"); return; }
    const h = Math.floor(ms / 3600000), m = Math.floor((ms % 3600000) / 60000);
    const txt = `${h}:${String(m).padStart(2, "0")}`;
    el.textContent = LANG === "bn" ? txt.replace(/\d/g, (d) => "০১২৩৪৫৬৭৮৯"[d]) : txt;
  });
}
tickCountdowns();
setInterval(tickCountdowns, 30000);

// ---------- mic and turns ----------
const mic = $("#mic");
const micText = $("#mic-text"), micText2 = $("#mic-text-2"), micTimer = $("#mic-timer");
let recorder = null, chunks = [], recStart = 0, recTick = null, recCap = null, player = null;
const MAX_SECONDS = 60;

function setMic(state, key, extra = "") {
  if (!mic) return;
  mic.dataset.state = state;
  const k = key || { idle: "mic.idle", recording: "mic.listening", uploading: "mic.uploading",
    thinking: "mic.thinking", speaking: "mic.speaking", error: "mic.error" }[state];
  micText.innerHTML = state === "thinking" ? `${S(k)} <span class="dots"><span>.</span><span>.</span><span>.</span></span>` : S(k);
  micText2.textContent = S(k, OTHER);
  mic.setAttribute("aria-label", S(k));
  micTimer.textContent = extra;
}

function stopPlayback() {
  if (player) { player.pause(); player = null; }
}

async function startRecording() {
  if (!navigator.mediaDevices || !window.MediaRecorder) { setMic("error", "mic.unsupported"); focusType(); return; }
  setMic("recording", null, "0:00");  // feedback within 300 ms of the tap
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    setMic("error", "mic.blocked"); focusType(); return;
  }
  const type = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]
    .find((t) => MediaRecorder.isTypeSupported(t)) || "";
  recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  chunks = [];
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  recorder.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    clearInterval(recTick); clearTimeout(recCap);
    const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
    recorder = null;
    if (blob.size < 1500) { setMic("idle"); return; }
    await sendTurn({ blob });
  };
  recorder.start();
  recStart = Date.now();
  recTick = setInterval(() => {
    const s = Math.floor((Date.now() - recStart) / 1000);
    micTimer.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} / 1:00`;
  }, 250);
  recCap = setTimeout(() => recorder && recorder.state === "recording" && recorder.stop(), MAX_SECONDS * 1000);
}

mic?.addEventListener("click", () => {
  const st = mic.dataset.state;
  if (st === "recording") { recorder && recorder.stop(); return; }
  if (st === "uploading" || st === "thinking") return;
  if (st === "speaking") stopPlayback();  // interruption: stop playback and record
  startRecording();
});

function focusType() { $("#type-input")?.focus(); }

function addBubble(role, text, decisionIds) {
  const chat = $("#chat");
  if (!chat) return null;
  const b = document.createElement("div");
  b.className = `bubble ${role}`;
  b.textContent = text;
  if (role === "sathi" && decisionIds && decisionIds.length) {
    b.append(document.createElement("br"));
    const a = document.createElement("a");
    a.href = `/why/${decisionIds[0]}`; a.textContent = S("card.why");
    b.append(a);
  }
  chat.append(b);
  b.scrollIntoView({ behavior: "smooth", block: "end" });
  return b;
}

async function sendTurn({ blob, text }) {
  setMic(blob ? "uploading" : "thinking");
  let pending = null;
  if (text) addBubble("farmer", text);
  try {
    let opts;
    if (blob) {
      const fd = new FormData();
      fd.append("audio", blob, "turn.webm");
      opts = { method: "POST", body: fd };
      setTimeout(() => mic && mic.dataset.state === "uploading" && setMic("thinking"), 600);
    } else {
      opts = { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) };
    }
    if (page === "talk") pending = addBubble("system", S("mic.thinking"));
    const r = await api("/api/turn", opts);
    pending?.remove();
    if (blob && r.transcript) addBubble("farmer", r.transcript);
    addBubble("sathi", r.reply_text, r.decision_ids);
    if (page === "home") await showHomeReply(r);
    if (r.reply_audio_b64) {
      setMic("speaking");
      player = new Audio(`data:audio/wav;base64,${r.reply_audio_b64}`);
      player.onended = () => { player = null; setMic("idle"); };
      player.onerror = () => { player = null; setMic("idle"); };
      try { await player.play(); } catch (e) { setMic("idle"); }
    } else {
      setMic("idle");
    }
  } catch (e) {
    pending?.remove();
    setMic("error", navigator.onLine === false ? "net.offline_short" : "mic.error");
    if (page === "talk") addBubble("system", e.message || S("error.generic"));
  }
}

async function showHomeReply(r) {
  const box = $("#reply-box");
  if (box) {
    box.hidden = false;
    const q = $("#reply-q");
    if (r.transcript) { q.hidden = false; q.textContent = r.transcript; }
    $("#reply-a").textContent = r.reply_text;
  }
  // Refresh the decision card in place.
  try {
    const html = await api("/", { headers: { Accept: "text/html" } });
    const doc = new DOMParser().parseFromString(html, "text/html");
    const fresh = doc.querySelector("#card-area");
    if (fresh) { $("#card-area").replaceWith(fresh); tickCountdowns(); }
    const fn = doc.querySelector(".farmname");
    if (fn) $(".farmname").textContent = fn.textContent;
  } catch (e) { /* keep the old card */ }
}

$("#type-form")?.addEventListener("submit", (e) => {
  e.preventDefault();
  const inp = $("#type-input");
  const text = inp.value.trim();
  if (!text) return;
  inp.value = "";
  stopPlayback();
  sendTurn({ text });
});
if (page === "talk") $("#chat")?.lastElementChild?.scrollIntoView({ block: "end" });

// ---------- claim kit ----------
if (page === "claim") {
  const id = document.body.dataset.claim;
  $$(".check input").forEach((cb) => cb.addEventListener("change", () =>
    api(`/api/claims/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ checklist: { [cb.dataset.key]: cb.checked } }) }).catch(() => { cb.checked = !cb.checked; })));
  $("#copy-report")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const txt = $("#report").textContent;
    try { await navigator.clipboard.writeText(txt); } catch (err) {
      const r = document.createRange(); r.selectNodeContents($("#report"));
      const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); document.execCommand("copy");
    }
    btn.textContent = btn.dataset.done;
    const copied = $('.check input[data-key="copied"]');
    if (copied && !copied.checked) { copied.checked = true; copied.dispatchEvent(new Event("change")); }
  });
  $("#mark-sent")?.addEventListener("click", async (e) => {
    await api(`/api/claims/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sent: e.currentTarget.dataset.sent === "true" }) });
    location.reload();
  });
}

// ---------- my farm ----------
if (page === "farm") {
  $("#farm-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const body = {};
    for (const [k, v] of fd.entries()) {
      if (v === "") continue;
      if (k === "insured") body[k] = v === "true";
      else if (k === "area_bigha" || k === "lat" || k === "lon") body[k] = Number(v);
      else body[k] = v;
    }
    await api("/api/farm", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    $("#farm-saved").hidden = false;
    setTimeout(() => location.reload(), 600);
  });
  $$('input[name="language"]').forEach((r) => r.addEventListener("change", async () => {
    await api("/api/lang", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lang: r.value }) });
    location.reload();
  }));
  $("#delete-farm")?.addEventListener("click", async (e) => {
    if (!confirm(e.currentTarget.dataset.confirm)) return;
    await api("/api/farm", { method: "DELETE" });
    location.href = "/";
  });
}

// ---------- operator view ----------
if (page === "ops") {
  $("#run-now")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget; btn.disabled = true;
    const out = $("#run-result");
    try {
      const r = await api("/api/ops/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      out.hidden = false; out.textContent = JSON.stringify(r, null, 2);
      setTimeout(() => location.reload(), 1500);
    } catch (err) { out.hidden = false; out.textContent = String(err.message); }
    btn.disabled = false;
  });
  $$(".src-toggle").forEach((cb) => cb.addEventListener("change", () =>
    api("/api/ops/source", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: cb.dataset.source, enabled: cb.checked }) }).catch(() => { cb.checked = !cb.checked; })));
}
