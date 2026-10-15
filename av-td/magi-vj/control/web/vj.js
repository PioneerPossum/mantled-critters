"use strict";
const $ = (s, r = document) => r.querySelector(s);
const HOLD_MS = 1200;
let S = null, prev = null, pendingMode = null, built = false;
let AUTH = {authed: false, user: null, configured: true};
const rows = new Map();          // addr -> {el, btn, ...}
const WORD = {auto: ["AI · AUTO", "自動"], assist: ["AI · ASSIST", "補助"], manual: ["HUMAN", "手動"]};
const NOTE = {
  manual: "The AI stops sending immediately. Arena keeps whatever values it has right now; nothing is reset.",
  assist: "The AI keeps driving the continuous macros, but clip triggers are only proposed in the log, never sent.",
  auto: "The AI drives continuous macros and bar-quantized clip triggers, within the tier caps. Only has an effect while the mood engine is running.",
};

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function say(t) { $("#live").textContent = t; toast(t); }
function toast(t, bad) {
  let box = $("#toasts"); if (!box) { box = el("div"); box.id = "toasts"; document.body.append(box); }
  const d = el("div", "toast" + (bad ? " bad" : ""), t); box.append(d); setTimeout(() => d.remove(), 4200);
}
async function api(path, body) {
  const r = await fetch(path, body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : undefined);
  const j = await r.json().catch(() => ({}));
  if (r.status === 401 && j.locked) { AUTH = {...AUTH, authed: false}; renderAuth(); openLogin(); }
  if (!r.ok) throw new Error(j.error || r.statusText); return j;
}

/* ---- sign-in lock: everything is viewable, changes need a signed-in user ---- */
function renderAuth() {
  document.body.classList.toggle("ro", !AUTH.authed); document.body.classList.toggle("rw", AUTH.authed);
  const b = $("#auth-badge"); b.textContent = AUTH.authed ? "UNLOCKED · " + AUTH.user.toUpperCase() : "LOCKED · READ ONLY";
  b.className = "badge " + (AUTH.authed ? "up" : "failed"); b.style.cssText = "font-size:14px;padding:3px 14px";
  $("#auth-btn").textContent = AUTH.authed ? "SIGN OUT" : "SIGN IN";
  $("#login-note").textContent = AUTH.configured ? "Control authority is production access." : "No users are set up yet: run vj-adduser on audire-videre.";
}
function openLogin() { const d = $("#login"); if (!d.open) { $("#login-err").hidden = true; $("#login-pw").value = ""; d.showModal(); $("#login-user").focus(); } }
$("#auth-btn").addEventListener("click", async () => {
  if (!AUTH.authed) { openLogin(); return; }
  await fetch("/api/vj/logout", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"}); AUTH = {...AUTH, authed: false, user: null}; renderAuth(); toast("Signed out: read only");
});
$("#login-cancel").addEventListener("click", () => $("#login").close());
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault(); const err = $("#login-err"); err.hidden = true;
  const r = await fetch("/api/vj/login", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({user: $("#login-user").value, password: $("#login-pw").value})});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { err.textContent = j.error || "sign in failed"; err.hidden = false; $("#login-pw").value = ""; return; }
  $("#login-pw").value = "";
  const me = await fetch("/api/vj/me", {cache: "no-store"}).then((x) => x.json()).catch(() => ({}));   // did the browser actually keep the session cookie?
  if (!me.authed) { err.textContent = "Signed in, but this browser did not keep the session (cookie blocked). Open this page directly at its own address (not inside another page) and make sure it is the https:// one if the site uses https."; err.hidden = false; return; }
  AUTH = {...AUTH, authed: true, user: me.user}; $("#login").close(); renderAuth(); toast("Signed in as " + me.user + ": controls unlocked");
});

/* ---- hold-to-confirm: arm on press, commit only if still held at HOLD_MS ---- */
function makeHold(btn, spec, onDone, msgEl) {
  let timer = null, tok = null, active = false;
  const setMsg = (t) => { if (msgEl) msgEl.textContent = t; };
  const cancel = (why) => {
    if (!active) return; active = false; clearTimeout(timer); tok = null;
    btn.classList.remove("holding"); btn.classList.add("cancelled"); setTimeout(() => btn.classList.remove("cancelled"), 320);
    if (why) setMsg(why);
  };
  async function start() {
    if (active || btn.disabled) return;
    if (!AUTH.authed) { openLogin(); return; }
    const s = spec(); if (!s) return;
    active = true; btn.style.setProperty("--hold", HOLD_MS + "ms"); btn.classList.remove("done");
    try { const a = await api("/api/vj/arm", s); tok = a.token; } catch (e) { active = false; toast(e.message, true); return; }
    if (!active) return;                          // released while arming
    btn.classList.add("holding"); setMsg("Keep holding…");
    timer = setTimeout(async () => {
      const t = tok; active = false; btn.classList.remove("holding"); btn.classList.add("done");
      try { const st = await api("/api/vj/commit", {token: t}); setMsg("Applied."); onDone(st); }
      catch (e) { toast(e.message, true); setMsg(e.message); }
      setTimeout(() => btn.classList.remove("done"), 700);
    }, HOLD_MS);
  }
  btn.addEventListener("pointerdown", (e) => { if (e.button === 0 || e.pointerType !== "mouse") { e.preventDefault(); start(); } });
  ["pointerup", "pointerleave", "pointercancel"].forEach((n) => btn.addEventListener(n, () => cancel("Released early: nothing changed.")));
  btn.addEventListener("contextmenu", (e) => e.preventDefault());
  btn.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); start(); } });
  btn.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") cancel("Released early: nothing changed."); });
  btn.addEventListener("blur", () => cancel());
  btn.addEventListener("click", (e) => e.preventDefault());
}

/* ---- authority ---- */
function renderAuthority() {
  const m = S.mode, a = $("#authority"), changed = prev && prev.mode !== m;
  a.dataset.mode = m;
  $("#auth-word").textContent = WORD[m][0]; $("#auth-kan").textContent = WORD[m][1];
  $("#auth-desc").textContent = S.modes[m];
  $("#auth-sub").textContent = S.mode_set ? "set via this page, the gateway reads it on every request" : "never set: defaults to MANUAL (the AI sends nothing)";
  document.querySelectorAll(".mode").forEach((b) => { b.setAttribute("aria-checked", String(b.dataset.mode === m)); b.classList.toggle("pending", b.dataset.mode === pendingMode); });
  if (changed) {
    const w = $("#auth-word"), box = $("#auth-readout"), wp = $(".wipe", box);
    [w, wp, box].forEach((x) => x.getAnimations?.().forEach((an) => an.cancel()));
    w.classList.remove("pop"); wp.classList.remove("go"); box.classList.remove("flash"); void w.offsetWidth;
    w.classList.add("pop"); wp.classList.add("go"); box.classList.add("flash");
    say(`Authority is now ${WORD[m][0]}`);
    if (pendingMode === m) closeDock();
  }
}
function openDock(mode) {
  if (!S || mode === S.mode) return;
  pendingMode = mode; $("#dock").hidden = false;
  $("#dock-title").textContent = `Set authority to ${WORD[mode][0]}`; $("#dock-note").textContent = NOTE[mode];
  $("#dock-msg").textContent = "Press and hold for about 1.2 seconds. Letting go early cancels.";
  $("#dock-hold").querySelector(".lab").textContent = mode === "manual" ? "HOLD TO TAKE CONTROL" : "HOLD TO RELEASE TO AI";
  renderAuthority();
}
function closeDock() { pendingMode = null; $("#dock").hidden = true; document.querySelectorAll(".mode").forEach((b) => b.classList.remove("pending")); }

/* ---- interaction table ---- */
const LOCK_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="shackle" d="M7 11V8a5 5 0 0 1 10 0v3"/><rect class="body" x="5" y="11" width="14" height="10" rx="1"/><circle class="key" cx="12" cy="16" r="1.5"/></svg>';
function buildTable() {
  const root = $("#table"); root.textContent = "";
  const GROUPS = [["macros", "Dashboard macros", "ダッシュ", "continuous · tier 1"], ["opacity", "Layer opacity", "不透明", "continuous · tier 1"], ["triggers", "Clip triggers", "起動", "discrete · tier 2, bar-quantized"]];
  for (const [key, title, jp, sub] of GROUPS) {
    const g = el("div", "grp"), h = el("div", "grp-h"); h.append(el("span", "hd", title), el("span", "cap", sub), el("span", "jp dim", jp)); g.append(h);
    for (const s of S.surfaces[key]) {
      const r = el("div", "row2"), lk = el("div", "lock"); lk.innerHTML = LOCK_SVG;
      const nm = el("div", "nm"); nm.append(el("b", null, s.label + (s.jp ? "  " + s.jp : "")), el("code", null, s.addr + "  ·  " + s.note));
      const owner = el("span", "owner hd", "AI");
      const btn = el("button", "hold sm"); btn.type = "button"; btn.append(el("i", "fill"), el("span", "lab hd", "HOLD TO LOCK"));
      btn.setAttribute("aria-label", "Hold to lock " + s.label);
      const sw = el("i", "sweep"); r.append(lk, nm, owner, btn, sw); g.append(r);
      rows.set(s.addr, {el: r, btn, owner, label: s.label});
      makeHold(btn, () => ({action: rows.get(s.addr).locked ? "unlock" : "lock", value: s.addr}), applyState, null);
    }
    root.append(g);
  }
  const g = el("div", "grp"), h = el("div", "grp-h"); h.append(el("span", "hd", "Human-only · gated"), el("span", "cap", "the AI can never touch these; no control here by design"), el("span", "jp dim", "禁止")); g.append(h);
  for (const x of S.gated) {
    const r = el("div", "row2 gated"), lk = el("div", "lock"); lk.innerHTML = LOCK_SVG;
    const nm = el("div", "nm"); nm.append(el("b", null, x.label), el("code", null, x.why));
    r.append(lk, nm, el("span", "owner hd", "GATED"), el("span")); g.append(r);
  }
  root.append(g); built = true;
}
function renderTable() {
  const locks = new Set(S.locks), plocks = new Set(prev ? prev.locks : S.locks);
  for (const [addr, o] of rows) {
    const L = locks.has(addr), was = plocks.has(addr);
    o.locked = L; o.el.classList.toggle("locked", L); o.el.classList.toggle("suspended", !L && S.mode === "manual");
    o.owner.textContent = L ? "HUMAN · LOCKED" : (S.mode === "manual" ? "AI SUSPENDED" : "AI");
    o.btn.querySelector(".lab").textContent = L ? "HOLD TO RELEASE" : "HOLD TO LOCK";
    o.btn.setAttribute("aria-label", (L ? "Hold to release " : "Hold to lock ") + o.label);
    if (prev && L !== was) {
      o.el.classList.remove("just-locked", "just-unlocked"); void o.el.offsetWidth;
      o.el.classList.add(L ? "just-locked" : "just-unlocked"); setTimeout(() => o.el.classList.remove("just-locked", "just-unlocked"), 1100);
      say(`${o.label} ${L ? "locked: the AI will not touch it" : "released to the AI"}`);
    }
  }
  const n = locks.size; $("#table-sub").textContent = n ? `${n} control${n > 1 ? "s" : ""} locked to you · the AI keeps the rest` : "AI-reachable controls · lock one to take it for yourself while the AI keeps the rest";
}

/* ---- AI suggestions (assist-mode proposals) + live reactions ---- */
let lastSug = null;
function renderSuggest() {
  const L = S.suggestions || [], box = $("#sug-list"); box.textContent = "";
  $("#sug-latest").value = L.length ? `${hms(L[0].t)}  ${L[0].text}` : "";
  $("#sug-sub").textContent = L.length ? `${L.length} proposal${L.length > 1 ? "s" : ""} on record · newest first` : "what the AI proposed but did not send (ASSIST mode)";
  if (!L.length) box.append(el("div", "dim", "Nothing proposed yet."));
  for (const x of L) { const r = el("div", "sug-r"); r.append(el("span", "dim", hms(x.t)), el("span", null, x.text)); box.append(r); }
  const newest = L.length ? L[0].t : 0;
  if (lastSug === null) lastSug = newest;                       // first load: show history, do not toast it
  else if (newest > lastSug) { for (const x of L.filter((y) => y.t > lastSug).reverse()) say("AI suggests: " + x.text); lastSug = newest; }
}
function renderReact() {
  const c = S.crowd || {counts: {crowd: {}, operator: {}}, sentiment: 0};
  const f = $("#react-fill"), v = Math.max(-1, Math.min(1, c.sentiment || 0));
  f.className = v < 0 ? "neg" : ""; f.style.width = Math.abs(v) * 50 + "%"; f.style.left = v < 0 ? (50 + v * 50) + "%" : "50%";
  const n = (k) => c.counts.crowd[k] || 0, op = Object.values(c.counts.operator).reduce((a, b) => a + b, 0);
  const tot = Object.values(c.counts.crowd).reduce((a, b) => a + b, 0) + op;
  $("#react-counts").textContent = tot ? `crowd: ${n("like")} love · ${n("more")} more · ${n("less")} less · ${n("dislike")} no` + (op ? ` · operator ${op}` : "") : "no reactions yet";
  $("#react-str").hidden = !AUTH.authed; $("#react-str-l").hidden = !AUTH.authed;
}
document.querySelectorAll("#react .react-btn").forEach((b) => b.addEventListener("click", async () => {
  try {
    const j = await api("/api/vj/react", {kind: b.dataset.k, strength: Number($("#react-str").value)});
    b.classList.add("sent"); setTimeout(() => b.classList.remove("sent"), 600);
    toast(j.source === "operator" ? `Sent: operator feedback (weight ${j.weight})` : "Sent: crowd reaction", false);
  } catch (e) { toast(e.message, true); }
}));

/* ---- activity ---- */
const hms = (t) => new Date(t * 1000).toLocaleTimeString([], {hour12: false});
function renderActivity() {
  const a = S.audio, box = $("#audio"); box.textContent = "";
  const cell = (k, v) => { const d = el("div"); d.append(el("span", "cap", k), el("b", null, v)); box.append(d); };
  if (a) { cell("BPM sent to Arena", S.tempo_sent ? Number(S.tempo_sent).toFixed(1) : "—"); cell("BPM detected", a.bpm_smooth ? a.bpm_smooth.toFixed(1) : (a.bpm ? a.bpm.toFixed(1) : "—")); cell("Level", a.rms_db > -100 ? a.rms_db.toFixed(1) + " dB" : "silence"); cell("Feed", a.active ? "ACTIVE" : "idle"); }
  else cell("Feed", "no data");
  const cl = $("#ctl-log"); cl.textContent = "";
  if (!S.control.length) cl.append(el("div", "dim", "No authority changes recorded yet."));
  for (const c of S.control) { const r = el("div", "ctl-r"); r.append(el("span", "dim", hms(c.t)), el("b", null, c.kind), el("span", null, c.detail), el("span", "dim", c.who)); cl.append(r); }
  const ac = $("#acts"); ac.textContent = ""; $("#act-sub").textContent = S.actions.length ? `(last ${S.actions.length}, newest first)` : "";
  if (!S.actions.length) ac.append(el("div", "dim", "No AI requests logged yet."));
  for (const x of S.actions) {
    const r = el("div", "r2 " + (x.decision || ""));
    r.append(el("span", "dim", hms(x.t)), el("span", null, "T" + x.tier), el("span", "a", (x.addr || "").replace("/composition", "")), el("span", null, (x.decision || "") + (x.live ? "" : " ·dry")));
    r.title = `${x.addr}  value=${x.value}  ${x.reason || ""}`; ac.append(r);
  }
}

function applyState(st) {
  document.body.classList.toggle("linkdown", st.link === "down");
  $("#linklost").hidden = st.link !== "down"; $("#linklost-why").textContent = st.error || "controls are disabled until the link returns";
  const b = $("#link-badge"); b.textContent = st.link === "down" ? "LINK LOST" : "LINKED"; b.className = "badge " + (st.link === "down" ? "failed" : "up");
  if (st.link === "down") return;
  if (st.auth) { AUTH = st.auth; renderAuth(); }
  S = st; if (!built) buildTable();
  renderAuthority(); renderTable(); renderActivity(); renderSuggest(); renderReact(); prev = {mode: S.mode, locks: [...S.locks]};
}
async function poll() { try { applyState(await api("/api/vj/state")); } catch (e) { applyState({link: "down", error: e.message}); } }

document.querySelectorAll(".mode").forEach((b) => b.addEventListener("click", () => { if (!AUTH.authed) { openLogin(); return; } openDock(b.dataset.mode); }));
renderAuth();
$("#dock-cancel").addEventListener("click", closeDock);
makeHold($("#dock-hold"), () => pendingMode ? {action: "mode", value: pendingMode} : null, applyState, $("#dock-msg"));
setInterval(() => { $("#clock").textContent = new Date().toLocaleTimeString([], {hour12: false}); }, 1000);
poll(); setInterval(poll, 2000);

/* =================== recorded shows: review viewer =================== */
const CATS = [["good", "Good"], ["too_much", "Too much"], ["too_little", "Too little"], ["bad_timing", "Bad timing"], ["missed_opportunity", "Missed opportunity"], ["note", "Note"]];
const SUGS = [["", "(no suggestion)"], ["clip_change", "change the clip"], ["calmer_clip", "switch to a calmer clip"], ["more_intensity", "more intensity"], ["less_intensity", "less intensity"], ["kaleido", "bring in kaleido"], ["twitch", "twitch hit"], ["hue_shift", "shift the hue"], ["hold_still", "hold still, change nothing"]];
const CAT_COL = {good: "#4dffc8", too_much: "#ff2d2d", too_little: "#ffb31a", bad_timing: "#ff5a1f", missed_opportunity: "#1fd1b0", note: "#b8734f"};
let SH = null, curCat = "good", opps = [], tlDur = 1;
const vid = $("#vid"), tl = $("#tl");
const mmss = (s) => { s = Math.max(0, s); return Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0"); };

async function loadShows() {
  let list = []; try { list = await api("/api/shows"); } catch (e) { return; }
  const box = $("#show-list"); box.textContent = "";
  if (!list.length) box.append(el("div", "dim", "No archived shows yet."));
  for (const s of list) {
    const c = el("button", "show-card" + (s.status === "needs_review" ? " nr" : "") + (s.status === "aborted" ? " ab" : "") + (SH && SH.id === s.id ? " open" : "")); c.type = "button";
    const chip = el("span", "chip2 " + (s.status === "needs_review" ? "nr" : s.status === "reviewed" ? "ok" : "ab"), s.status === "needs_review" ? "NEEDS REVIEW" : s.status === "reviewed" ? "REVIEWED" : "ABORTED");
    c.append(chip, el("div", "hd", s.title), el("div", "meta", `${s.start.slice(5)} → ${s.end.slice(11)} · ${mmss(s.duration_s)} · ${s.counts.tier2_sent} clip triggers · ${s.feedback_count} feedback`),
      el("div", "meta", s.video ? `video ${s.video.size_mb} MB (720p60)` : "no video"));
    c.addEventListener("click", () => openShow(s.id)); box.append(c);
  }
}
async function openShow(id) {
  SH = await api("/api/shows/" + id); curCat = "good";
  $("#viewer").hidden = false; $("#v-title").textContent = SH.title;
  $("#v-sub").textContent = `${SH.start} → ${SH.end} · ${SH.counts.live} live requests · ${SH.counts.tier2_sent} clip triggers sent`;
  $("#v-note").textContent = SH.note || "";
  const st = $("#v-status"); st.className = "badge " + (SH.status === "needs_review" ? "busy" : SH.status === "reviewed" ? "up" : "disabled");
  st.textContent = SH.status === "needs_review" ? "NEEDS REVIEW" : SH.status.toUpperCase();
  const rv = $("#v-review"); rv.textContent = SH.status === "reviewed" ? "Reopen for review" : "Mark as reviewed"; rv.hidden = SH.status === "aborted";
  const hasV = !!SH.video; vid.hidden = !hasV; $("#v-novideo").hidden = hasV;
  if (hasV) { vid.src = `/media/${SH.id}/${SH.video.file}`; tlDur = SH.video.clip_duration; } else { vid.removeAttribute("src"); tlDur = SH.duration_s; }
  buildCats(); computeOpps(); renderFbList(); drawTL(); $("#viewer").scrollIntoView({behavior: "smooth", block: "start"});
  loadShows();
}
const t0v = () => (SH.video ? SH.video.clip_start : SH.t0);
const toV = (tw) => tw - t0v();
function buildCats() {
  const c = $("#cats"); c.textContent = "";
  for (const [k, lab] of CATS) { const b = el("button", "cat " + k, lab); b.type = "button"; b.setAttribute("role", "radio"); b.setAttribute("aria-checked", String(k === curCat));
    b.addEventListener("click", () => { curCat = k; c.querySelectorAll(".cat").forEach((x) => x.setAttribute("aria-checked", String(x === b))); syncSug(); }); c.append(b); }
  const sel = $("#fb-sug"); sel.textContent = ""; for (const [k, lab] of SUGS) { const o = el("option", null, lab); o.value = k; sel.append(o); } syncSug();
}
function syncSug() { const on = ["missed_opportunity", "too_little", "bad_timing"].includes(curCat); $("#fb-sug").hidden = !on; $("#fb-sug-l").hidden = !on; }

/* candidate control opportunities: music energy shifts (>= 6 dB over 8 s) with no clip trigger within 10 s */
function computeOpps() {
  opps = []; const a = (SH.audio || []).filter((x) => x.t && x.rms_db > -100);
  const trig = SH.actions.filter((x) => x.tier === 2 && x.decision === "sent").map((x) => toV(x.t));
  let lastOp = -99;
  for (let i = 4; i < a.length; i++) {
    const d = a[i].rms_db - a[i - 4].rms_db, tv = toV(a[i].t);
    if (tv > 12 && Math.abs(d) >= 6 && tv - lastOp > 12 && !trig.some((t) => Math.abs(t - tv) < 10)) { opps.push({t_video: tv, text: `music ${d > 0 ? "rose" : "dropped"} ${Math.abs(d).toFixed(0)} dB in 8 s, no clip change nearby`}); lastOp = tv; }
  }
}
function renderFbList() {
  const L = $("#fb-list"); L.textContent = "";
  const items = [...SH.feedback.map((f) => ({...f, _k: "fb"})), ...opps.map((o) => ({...o, _k: "op"}))].sort((a, b) => (a.t_video ?? 1e9) - (b.t_video ?? 1e9));
  if (!items.length) L.append(el("div", "dim", "Nothing yet. Pause the video where you'd have wanted the AI to act and add feedback."));
  for (const f of items) {
    const r = el("div", "fb-item " + (f._k === "op" ? "op" : f.kind === "remark" ? "rm" : "")); const tv = f.t_video ?? (f.t_wall ? toV(f.t_wall) : 0);
    const tb = el("button", "t", mmss(tv)); tb.type = "button"; tb.title = "Jump to this moment"; tb.addEventListener("click", () => { vid.currentTime = Math.max(0, tv - 1); vid.pause(); });
    const lab = f._k === "op" ? "possible opportunity" : (f.source === "crowd" ? "crowd · " + ({like: "loved it", dislike: "not feeling it", more: "wanted more", less: "wanted less"}[f.reaction] || f.reaction) : f.kind === "remark" ? "live remark (Gil)" : (f.source === "operator" && f.weight ? `operator ×${f.weight} · ` : "") + (CATS.find((c) => c[0] === f.category) || [0, f.category])[1] + (f.suggest ? " → " + (SUGS.find((s) => s[0] === f.suggest) || [0, f.suggest])[1] : ""));
    const body = el("div", null, f._k === "op" ? f.text : (f.text || "")); const cl = el("span", "cat-l", lab); cl.style.color = f._k === "op" ? "#1fd1b0" : (CAT_COL[f.category] || "#ffb31a");
    r.append(tb, cl, body);
    if (f._k === "fb" && f.kind !== "remark") { const x = el("button", "x", "✕"); x.type = "button"; x.title = "Delete this feedback"; x.addEventListener("click", async () => { if (!confirm("Delete this feedback item?")) return; await api(`/api/shows/${SH.id}/feedback/${f.id}/delete`, {}); await refreshShow(); }); r.append(x); }
    else if (f._k === "op") { const x = el("button", "x", "＋"); x.type = "button"; x.title = "Give feedback here"; x.addEventListener("click", () => { vid.currentTime = Math.max(0, tv - 1); vid.pause(); curCat = "missed_opportunity"; buildCats(); $("#fb-text").focus(); }); r.append(x); }
    else r.append(el("span"));
    L.append(r);
  }
}
async function refreshShow() { const keep = vid.currentTime; SH = await api("/api/shows/" + SH.id); computeOpps(); renderFbList(); drawTL(); loadShows(); void keep; }

/* ---- timeline canvas ---- */
function drawTL() {
  if (!SH) return; const dpr = window.devicePixelRatio || 1, W = tl.clientWidth * dpr, H = 150 * dpr; tl.width = W; tl.height = H;
  const g = tl.getContext("2d"), X = (tv) => (tv / tlDur) * W, y = (v, top, h) => top + h * (1 - v);
  g.clearRect(0, 0, W, H); g.font = `${11 * dpr}px IBM Plex Mono, monospace`;
  g.strokeStyle = "#4a1d0d"; g.lineWidth = 1; for (let s = 0; s <= tlDur; s += 30) { g.beginPath(); g.moveTo(X(s), 0); g.lineTo(X(s), H); g.stroke(); g.fillStyle = "#b8734f"; g.fillText(mmss(s), X(s) + 3 * dpr, H - 3 * dpr); }
  const au = (SH.audio || []).filter((x) => x.t && x.rms_db > -100);                                   // energy area (bottom)
  if (au.length) { g.beginPath(); g.moveTo(X(toV(au[0].t)), H - 16 * dpr); for (const p of au) g.lineTo(X(toV(p.t)), y(Math.min(1, Math.max(0, (p.rms_db + 45) / 40)), 70 * dpr, 64 * dpr)); g.lineTo(X(toV(au[au.length - 1].t)), H - 16 * dpr); g.closePath(); g.fillStyle = "rgba(255,216,191,.22)"; g.fill(); }
  const COL = {link2: "#1fd1b0", link5: "#ff5a1f", link7: "#ffb31a"};                                  // macro lines (top)
  for (const [lk, col] of Object.entries(COL)) {
    const pts = SH.actions.filter((a) => a.tier === 1 && a.decision === "sent" && a.addr.endsWith("/" + lk)); if (!pts.length) continue;
    g.beginPath(); pts.forEach((p, i) => { const px = X(toV(p.t)), py = y(Math.min(1, p.value), 8 * dpr, 52 * dpr); i ? g.lineTo(px, py) : g.moveTo(px, py); }); g.strokeStyle = col; g.lineWidth = 1.5 * dpr; g.stroke();
  }
  for (const a of SH.actions.filter((a) => a.tier === 2 && a.decision === "sent")) {                    // clip triggers
    const px = X(toV(a.t)); g.strokeStyle = "#4dffc8"; g.lineWidth = 2 * dpr; g.beginPath(); g.moveTo(px, 0); g.lineTo(px, H - 16 * dpr); g.stroke();
    const m = a.addr.match(/layers\/(\d+)\/clips\/(\d+)/); if (m) { g.fillStyle = "#4dffc8"; g.fillText(`L${m[1]}C${m[2]}`, px + 3 * dpr, 12 * dpr); }
  }
  g.setLineDash([4 * dpr, 4 * dpr]); g.strokeStyle = "#1fd1b0"; g.lineWidth = dpr; for (const o of opps) { const px = X(o.t_video); g.beginPath(); g.moveTo(px, 14 * dpr); g.lineTo(px, H - 16 * dpr); g.stroke(); } g.setLineDash([]);
  for (const f of SH.feedback) {
    const tv = f.t_video ?? toV(f.t_wall || 0), px = X(tv);
    if (f.source === "crowd") { g.strokeStyle = "#7fd0ff"; g.lineWidth = 1.5 * dpr; g.beginPath(); g.arc(px, H - 36 * dpr, 3.5 * dpr, 0, 7); g.stroke(); continue; }   // crowd: hollow, small, own row
    g.fillStyle = CAT_COL[f.category] || "#ffb31a"; g.beginPath(); g.arc(px, H - 22 * dpr, (4 + 1.5 * (f.weight || 1)) * dpr, 0, 7); g.fill();            // operator: filled, size = weight
  }
  const ph = X(vid.currentTime || 0); g.strokeStyle = "#ff2d2d"; g.lineWidth = 2 * dpr; g.beginPath(); g.moveTo(ph, 0); g.lineTo(ph, H); g.stroke();
}
tl.addEventListener("click", (e) => { const r = tl.getBoundingClientRect(); vid.currentTime = ((e.clientX - r.left) / r.width) * tlDur; });
tl.addEventListener("mousemove", (e) => {
  if (!SH) return; const r = tl.getBoundingClientRect(), tv = ((e.clientX - r.left) / r.width) * tlDur, tip = $("#tl-tip");
  const near = SH.actions.filter((a) => a.tier === 2 && a.decision === "sent").find((a) => Math.abs(toV(a.t) - tv) < tlDur * 0.01);
  tip.hidden = false; tip.style.left = Math.min(r.width - 150, e.clientX - r.left + 8) + "px"; tip.textContent = mmss(tv) + (near ? "  " + near.addr.replace("/composition", "") : "");
});
tl.addEventListener("mouseleave", () => { $("#tl-tip").hidden = true; });
["timeupdate", "seeked", "pause", "play"].forEach((n) => vid.addEventListener(n, () => { $("#fb-time").textContent = mmss(vid.currentTime); drawTL(); }));
window.addEventListener("resize", drawTL);
$("#fb-add").addEventListener("click", async () => {
  if (!SH) return; const msg = $("#fb-msg");
  try { await api(`/api/shows/${SH.id}/feedback`, {t_video: vid.currentTime || 0, category: curCat, suggest: $("#fb-sug").hidden ? "" : $("#fb-sug").value, text: $("#fb-text").value, strength: Number($("#fb-strength").value)});
    $("#fb-text").value = ""; msg.textContent = "Saved at " + mmss(vid.currentTime); await refreshShow(); say("Feedback saved"); } catch (e) { msg.textContent = e.message; toast(e.message, true); }
});
$("#v-close").addEventListener("click", () => { $("#viewer").hidden = true; vid.pause(); SH = null; loadShows(); });
$("#v-review").addEventListener("click", async () => { const next = SH.status === "reviewed" ? "needs_review" : "reviewed"; await api(`/api/shows/${SH.id}/status`, {status: next}); await openShow(SH.id); });
loadShows();
if (location.hash.startsWith("#show=")) openShow(decodeURIComponent(location.hash.slice(6))).catch(() => {});

/* crowd markers on a recorded show: anyone, reaction kinds only */
document.querySelectorAll("#fb-crowd .react-btn").forEach((b) => b.addEventListener("click", async () => {
  if (!SH) return; const m = $("#fb-crowd-msg");
  try { await api(`/api/shows/${SH.id}/reaction`, {kind: b.dataset.k, t_video: vid.currentTime || 0}); m.textContent = `Marked ${mmss(vid.currentTime || 0)} as ${b.textContent.toLowerCase()} (crowd marker).`; await refreshShow(); }
  catch (e) { m.textContent = e.message; toast(e.message, true); }
}));
