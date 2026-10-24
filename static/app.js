"use strict";
// Vicolocal dashboard. Browser does WebRTC directly with the camera over the LAN; the server
// only bridges signaling (see vico_server.py / vico_signaling.py).

const $ = (s) => document.querySelector(s);
const grid = $("#grid");
const LS = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
             set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };

// ---------- config (env-settable via /api/config) ----------
let CFG = { batteryWarn: true, simple: false };
async function loadConfig() {
  try {
    const c = await (await fetch("/api/config")).json();
    CFG = Object.assign(CFG, c);
    if (c.brand) { document.title = c.brand; if (c.brand !== "Vicolocal") $(".wordmark").textContent = c.brand; }
    if (c.tagline != null) $(".brand .sub").textContent = c.tagline;
    if (c.theme && !LS.get("vt")) { document.documentElement.dataset.theme = c.theme; themeSel.value = c.theme; }
    if (c.accent) document.documentElement.style.setProperty("--accent", c.accent);
    if (c.accent2) document.documentElement.style.setProperty("--accent2", c.accent2);
    if (c.simple) document.body.classList.add("simple");
  } catch {}
}

// ---------- theme ----------
const themeSel = $("#theme");
const savedTheme = LS.get("vt");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
themeSel.value = document.documentElement.dataset.theme;
themeSel.addEventListener("change", () => {
  document.documentElement.dataset.theme = themeSel.value;
  LS.set("vt", themeSel.value);
});

// ---------- toast ----------
let toastT;
function toast(msg) {
  const el = $("#toast"); el.textContent = msg; el.classList.remove("hidden");
  clearTimeout(toastT); toastT = setTimeout(() => el.classList.add("hidden"), 4000);
}

// ---------- camera list + ordering ----------
function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }
function ageText(ms) {
  if (!ms) return "";
  const m = Math.round((Date.now() - (ms > 1e12 ? ms : ms * 1000)) / 60000);
  return m < 1 ? "just now" : m < 60 ? m + "m ago" : Math.round(m / 60) + "h ago";
}
function savedOrder() { try { return JSON.parse(LS.get("vorder") || "[]"); } catch { return []; } }
function applyOrder(cams) {
  const order = savedOrder();
  const idx = (s) => { const i = order.indexOf(s); return i === -1 ? 1e6 : i; };
  return cams.slice().sort((a, b) => (idx(a.serial) - idx(b.serial)) || (Number(b.online) - Number(a.online)));
}
function persistOrder() {
  LS.set("vorder", JSON.stringify([...grid.children].map((c) => c.dataset.serial)));
}

async function loadCameras(force) {
  let cams;
  try { cams = await (await fetch("/api/cameras" + (force ? "?refresh=1" : ""))).json(); }
  catch (e) { toast("Could not load cameras: " + e); return; }
  grid.innerHTML = "";
  for (const cam of applyOrder(cams)) grid.appendChild(renderCard(cam));
}

function renderCard(cam) {
  const card = document.createElement("div");
  card.className = "card" + (cam.online ? "" : " offline");
  card.dataset.serial = cam.serial;
  card._cam = cam;
  const battLow = typeof cam.battery === "number" && cam.battery <= 20 && cam.onBattery;
  card.innerHTML = `
    <div class="thumb" ${cam.thumbUrl ? `style="background-image:url('${cam.thumbUrl}')"` : ""}>
      ${cam.thumbUrl ? "" : '<div class="noimg">no snapshot</div>'}
      ${cam.thumbTime ? `<div class="thumb-age">${ageText(cam.thumbTime)}</div>` : ""}
      <div class="play">&#x25b6;</div>
      <div class="overlay">
        <span class="name">${esc(cam.name)}</span>
        <span class="badges">
          ${typeof cam.battery === "number"
            ? `<span class="badge ${cam.charging ? "charging" : ""} ${battLow ? "batt-low" : ""}" title="${cam.charging ? "Charging" : "On battery"}">${cam.charging ? "⚡" : "🔋"} ${cam.battery}%</span>`
            : ""}
          ${cam.online ? "" : '<span class="badge off">offline</span>'}
        </span>
      </div>
    </div>`;
  attachDrag(card);
  return card;
}

// ---------- pointer-based drag reorder (full mode only; kid mode just taps) ----------
function clearDropTargets() { for (const c of grid.children) c.classList.remove("drop-target"); }

// Move `card` next to whichever other tile the pointer is closest to, choosing a
// side by reading order (row below -> after, row above -> before, same row -> by x).
function reorderTo(card, e) {
  let best = null, bestD = Infinity, bestR = null;
  for (const c of grid.children) {
    if (c === card) continue;
    const r = c.getBoundingClientRect();
    const d = Math.hypot(e.clientX - (r.left + r.width / 2), e.clientY - (r.top + r.height / 2));
    if (d < bestD) { bestD = d; best = c; bestR = r; }
  }
  clearDropTargets();
  if (!best) return;
  let after;
  if (e.clientY > bestR.bottom) after = true;
  else if (e.clientY < bestR.top) after = false;
  else after = e.clientX > bestR.left + bestR.width / 2;
  best.classList.add("drop-target");
  grid.insertBefore(card, after ? best.nextSibling : best);
}

function attachDrag(card) {
  let start = null, dragging = false;
  card.addEventListener("dragstart", (e) => e.preventDefault());  // no native text/img drag
  card.addEventListener("pointerdown", (e) => {
    if (e.button != null && e.button !== 0) return;               // left button / touch only
    start = { x: e.clientX, y: e.clientY, id: e.pointerId };
    dragging = false;
  });
  card.addEventListener("pointermove", (e) => {
    if (!start || start.id !== e.pointerId) return;
    if (!dragging) {
      if (Math.hypot(e.clientX - start.x, e.clientY - start.y) <= 8) return;  // tap vs drag
      if (document.body.classList.contains("simple")) { start = null; return; } // kid mode: no reorder
      dragging = true;
      grid.classList.add("reordering");
      card.classList.add("dragging");
      try { card.setPointerCapture(e.pointerId); } catch {}
    }
    e.preventDefault();
    reorderTo(card, e);
  });
  const end = (e) => {
    if (!start || start.id !== e.pointerId) return;
    const wasDragging = dragging;
    start = null; dragging = false;
    card.classList.remove("dragging");
    grid.classList.remove("reordering");
    clearDropTargets();
    if (wasDragging) { persistOrder(); return; }
    openCamera(card._cam);   // tap: offline cameras open straight to Recordings
  };
  card.addEventListener("pointerup", end);
  card.addEventListener("pointercancel", end);
}

// ---------- zoom / pan on the video ----------
function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
function setupZoom(wrap, video, badge) {
  let scale = 1, tx = 0, ty = 0;
  const pts = new Map(); let pinch = null, pan = null; const MAX = 6;
  function apply() {
    const mx = (scale - 1) * wrap.clientWidth / 2, my = (scale - 1) * wrap.clientHeight / 2;
    tx = Math.max(-mx, Math.min(mx, tx)); ty = Math.max(-my, Math.min(my, ty));
    video.style.transform = `translate(${tx}px,${ty}px) scale(${scale})`;
    wrap.classList.toggle("zoomed", scale > 1.01);
    badge.classList.toggle("hidden", scale <= 1.01);
    badge.textContent = scale.toFixed(1) + "×";
  }
  function zoomAt(cx, cy, f) {
    const p = scale; scale = Math.max(1, Math.min(MAX, scale * f));
    tx = cx - (scale / p) * (cx - tx); ty = cy - (scale / p) * (cy - ty); apply();
  }
  wrap.addEventListener("wheel", (e) => {
    e.preventDefault(); const r = wrap.getBoundingClientRect();
    zoomAt(e.clientX - r.left - r.width / 2, e.clientY - r.top - r.height / 2, e.deltaY < 0 ? 1.12 : 1 / 1.12);
  }, { passive: false });
  wrap.addEventListener("dblclick", () => { scale = 1; tx = 0; ty = 0; apply(); });
  wrap.addEventListener("pointerdown", (e) => {
    try { wrap.setPointerCapture(e.pointerId); } catch {}
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size === 1 && scale > 1) { pan = { x: e.clientX, y: e.clientY, tx, ty }; wrap.classList.add("panning"); }
    if (pts.size === 2) { const p = [...pts.values()]; pinch = { d: dist(p[0], p[1]), s: scale }; }
  });
  wrap.addEventListener("pointermove", (e) => {
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const p = [...pts.values()];
    if (pts.size === 2 && pinch) {
      const r = wrap.getBoundingClientRect();
      const mid = { x: (p[0].x + p[1].x) / 2 - r.left - r.width / 2, y: (p[0].y + p[1].y) / 2 - r.top - r.height / 2 };
      const target = Math.max(1, Math.min(MAX, pinch.s * dist(p[0], p[1]) / pinch.d));
      zoomAt(mid.x, mid.y, target / scale);
    } else if (pts.size === 1 && pan) { tx = pan.tx + (e.clientX - pan.x); ty = pan.ty + (e.clientY - pan.y); apply(); }
  });
  const up = (e) => { pts.delete(e.pointerId); if (pts.size < 2) pinch = null; if (!pts.size) { pan = null; wrap.classList.remove("panning"); } };
  wrap.addEventListener("pointerup", up); wrap.addEventListener("pointercancel", up);
  return { reset() { scale = 1; tx = 0; ty = 0; apply(); } };
}
let zoomer = null;

// ---------- streaming ----------
let session = null;
function dbg(kind, msg) {
  const log = $("#debug-log"); const line = document.createElement("span");
  line.className = kind; line.textContent = `${new Date().toLocaleTimeString()} ${msg}\n`;
  log.appendChild(line); log.scrollTop = log.scrollHeight;
}
function setState(text, live) {
  const el = $("#stage-state"); el.lastChild.textContent = " " + text;
  el.classList.toggle("live", !!live);
}

// ---------- per-camera live audio ----------
// Sound is off by default (quiet, autoplay-friendly) and remembered per camera.
function soundPref(serial) { return LS.get("vsound:" + serial) === "1"; }
function updateSoundBtn(on, show) {
  const b = $("#sound-toggle");
  b.classList.toggle("hidden", !show);
  b.classList.toggle("on", !!on);
  b.innerHTML = on ? "&#x1f50a;" : "&#x1f507;";   // 🔊 / 🔇
  b.title = on ? "Sound on" : "Sound off";
}
function applySound(cam) {
  if (!cam) return;
  const on = soundPref(cam.serial);
  const vid = $("#video");
  vid.muted = !on;
  if (on) vid.play().catch(() => {});
  updateSoundBtn(on, true);
}
function toggleSound() {
  if (!currentCam) return;
  const on = !soundPref(currentCam.serial);
  LS.set("vsound:" + currentCam.serial, on ? "1" : "0");
  applySound(currentCam);
  toast(on ? "Sound on — " + currentCam.name : "Sound muted");
}

let currentCam = null, hls = null;

function openCamera(cam) {
  closeStream(); stopClip();
  currentCam = cam;
  $("#stage").classList.remove("hidden");
  $("#stage-name").textContent = cam.name;
  if (!zoomer) zoomer = setupZoom($("#video-wrap"), $("#video"), $("#zoom-badge"));
  setTab(cam.online ? "live" : "rec");
}

function setTab(tab) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
  if (tab === "live") {
    stopClip();
    $("#rec-panel").classList.add("hidden"); $("#clip-bar").classList.add("hidden");
    $("#video-wrap").classList.remove("hidden");
    if (currentCam.online) startLive(currentCam);
    else { $("#video-wrap").classList.add("hidden"); $("#spinner").classList.add("hidden"); updateSoundBtn(false, false); setState("offline — recordings only", false); }
  } else {
    closeStream(); stopClip();
    updateSoundBtn(false, false);
    $("#video-wrap").classList.add("hidden"); $("#battery-warn").classList.add("hidden"); $("#clip-bar").classList.add("hidden");
    $("#rec-panel").classList.remove("hidden");
    setState("recordings", false);
    loadClips(currentCam.serial);
  }
}

async function startLive(cam) {
  closeStream();
  $("#debug-log").textContent = ""; $("#debug-stats").textContent = "";
  $("#spinner").classList.remove("hidden");
  const vid = $("#video"); vid.srcObject = null; vid.controls = false; vid.muted = true;
  updateSoundBtn(soundPref(cam.serial), true);   // show sound toggle for this camera
  zoomer.reset();
  setState("waking camera…", false);

  const warn = $("#battery-warn");
  if (cam.onBattery && CFG.batteryWarn !== false) {
    warn.classList.remove("hidden");
    warn.innerHTML = `&#x1f50b; <b>${esc(cam.name)}</b> is running on battery` +
      (typeof cam.battery === "number" ? ` (${cam.battery}%)` : "") +
      `. Live streaming drains it — close the view when you're done.`;
  } else warn.classList.add("hidden");

  const pc = new RTCPeerConnection({ iceServers: [], bundlePolicy: "max-bundle" });
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/${cam.serial}`);
  const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  session = { ws, pc, audioCtx, statsTimer: null };

  try {   // camera needs a sendrecv audio track with a real ssrc -> feed it silence
    if (audioCtx.state === "suspended") audioCtx.resume();   // autoplay policy (Fire/mobile)
    const dst = audioCtx.createMediaStreamDestination();
    const osc = audioCtx.createOscillator(), g = audioCtx.createGain();
    g.gain.value = 0; osc.connect(g); g.connect(dst); osc.start();
    pc.addTrack(dst.stream.getAudioTracks()[0], dst.stream);
  } catch (e) { dbg("err", "silent audio failed: " + e); }
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.createDataChannel("dataChannel");

  pc.ontrack = (e) => {
    const vid = $("#video");
    // Collect audio + video into one stream we control, so sound plays when unmuted.
    if (!session.remoteStream) session.remoteStream = new MediaStream();
    session.remoteStream.addTrack(e.track);
    vid.srcObject = session.remoteStream;
    dbg("pc", "track " + e.track.kind);
    if (e.track.kind === "video") {
      $("#spinner").classList.add("hidden"); setState("live", true);
      applySound(currentCam);            // honor this camera's saved sound setting
      vid.play().catch(() => {});
    }
  };
  pc.onicecandidate = (e) => {
    if (e.candidate && ws.readyState === 1) ws.send(JSON.stringify({
      type: "candidate", candidate: e.candidate.candidate, sdpMid: e.candidate.sdpMid, sdpMLineIndex: e.candidate.sdpMLineIndex }));
  };
  pc.oniceconnectionstatechange = () => dbg("pc", "ICE " + pc.iceConnectionState);
  pc.onconnectionstatechange = () => {
    dbg("pc", "connection " + pc.connectionState);
    if (pc.connectionState !== "connected") setState(pc.connectionState, false);
    if (["failed", "disconnected", "closed"].includes(pc.connectionState)) $("#spinner").classList.add("hidden");
  };

  ws.onmessage = async (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "ready") {
      setState("negotiating…", false);
      await pc.setLocalDescription(await pc.createOffer());
      ws.send(JSON.stringify({ type: "offer", sdp: pc.localDescription.sdp })); dbg("pc", "sent offer");
    } else if (m.type === "answer") {
      await pc.setRemoteDescription({ type: "answer", sdp: m.sdp }); dbg("pc", "got answer"); startStats();
    } else if (m.type === "candidate") {
      try { await pc.addIceCandidate({ candidate: m.candidate, sdpMid: m.sdpMid, sdpMLineIndex: m.sdpMLineIndex }); }
      catch (e) { dbg("err", "addIceCandidate: " + e); }
    } else if (m.type === "log") { dbg("cam", m.msg); }
    else if (m.type === "error") { dbg("err", m.msg); setState("error — try again", false); $("#spinner").classList.add("hidden"); toast(m.msg); }
    else if (m.type === "closed") { dbg("cam", "camera closed: " + (m.reason || "")); }
  };
  ws.onerror = () => dbg("err", "signal socket error");

  function startStats() {
    session.statsTimer = setInterval(async () => {
      if (!session || pc.connectionState === "closed") return;
      const stats = await pc.getStats(); let pair, inVid;
      stats.forEach((r) => {
        if (r.type === "candidate-pair" && r.nominated && r.state === "succeeded") pair = r;
        if (r.type === "inbound-rtp" && r.kind === "video") inVid = r;
      });
      const parts = [];
      if (pair) {
        const rc = stats.get(pair.remoteCandidateId), lc = stats.get(pair.localCandidateId);
        const relay = (rc && rc.candidateType === "relay") || (lc && lc.candidateType === "relay");
        parts.push(`<b>path</b> ${relay ? "RELAY (cloud)" : "LAN direct"}`);
        if (rc) parts.push(`<b>camera</b> ${rc.address || rc.ip || "?"}`);
      }
      if (inVid) {
        parts.push(`<b>recv</b> ${(inVid.bytesReceived / 1024 | 0)} KiB`);
        if (inVid.framesPerSecond != null) parts.push(`<b>fps</b> ${inVid.framesPerSecond}`);
        if (inVid.packetsLost != null) parts.push(`<b>lost</b> ${inVid.packetsLost}`);
      }
      const v = $("#video"); if (v.videoWidth) parts.push(`<b>res</b> ${v.videoWidth}×${v.videoHeight}`);
      $("#debug-stats").innerHTML = parts.join(" &nbsp; ");
    }, 1000);
  }
}

function closeStream() {
  if (!session) return;
  try { if (session.ws.readyState === 1) session.ws.send(JSON.stringify({ type: "bye" })); } catch {}
  try { session.ws.close(); } catch {}
  try { session.pc.close(); } catch {}
  try { session.audioCtx.close(); } catch {}
  clearInterval(session.statsTimer); session = null; $("#video").srcObject = null;
}
function closeStage() { closeStream(); stopClip(); $("#stage").classList.add("hidden"); }

// ---------- recordings ----------
async function loadClips(serial) {
  const list = $("#rec-list");
  list.innerHTML = '<div class="rec-empty">loading…</div>';
  let clips;
  try { clips = await (await fetch(`/api/clips?serial=${serial}&days=3`)).json(); }
  catch { list.innerHTML = '<div class="rec-empty">could not load recordings</div>'; return; }
  if (clips.error) { list.innerHTML = `<div class="rec-empty">${esc(clips.error)}</div>`; return; }
  if (!clips.length) { list.innerHTML = '<div class="rec-empty">no recordings in the last few days</div>'; return; }
  list.innerHTML = "";
  for (const c of clips) {
    const el = document.createElement("div"); el.className = "clip";
    const t = new Date(c.time * 1000);
    el.innerHTML = `
      <div class="cthumb" ${c.thumb ? `style="background-image:url('${c.thumb}')"` : ""}>
        <div class="cplay">&#x25b6;</div>
        <div class="cdur">${c.duration}s</div>
      </div>
      <div class="cmeta">
        <div class="ctime">${t.toLocaleDateString([], { month: "short", day: "numeric" })} ${t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</div>
        ${c.tags && c.tags.length ? `<div class="ctags">${esc(c.tags.join(", "))}</div>` : ""}
      </div>`;
    el.addEventListener("click", () => playClip(c));
    list.appendChild(el);
  }
}

function playClip(c) {
  stopClip();
  $("#rec-panel").classList.add("hidden");
  $("#video-wrap").classList.remove("hidden");
  $("#clip-bar").classList.remove("hidden");
  $("#spinner").classList.add("hidden");
  zoomer && zoomer.reset();
  const v = $("#video");
  v.srcObject = null; v.muted = false; v.controls = true;
  const t = new Date(c.time * 1000);
  $("#clip-meta").textContent = `${t.toLocaleString()} · ${c.duration}s`;
  $("#clip-dl").href = `/api/clip/download?url=${encodeURIComponent(c.video)}&name=` +
    encodeURIComponent(`${currentCam.name || "clip"} ${t.toISOString().slice(0, 16).replace("T", " ")}`);
  if (v.canPlayType("application/vnd.apple.mpegurl")) { v.src = c.video; v.play().catch(() => {}); }
  else if (window.Hls && Hls.isSupported()) {
    hls = new Hls({ maxBufferLength: 20 });
    hls.loadSource(c.video); hls.attachMedia(v);
    hls.on(Hls.Events.MANIFEST_PARSED, () => v.play().catch(() => {}));
  } else { toast("This browser can't play HLS"); }
}

function stopClip() {
  if (hls) { try { hls.destroy(); } catch {} hls = null; }
  const v = $("#video");
  v.controls = false; v.muted = true;
  if (v.getAttribute("src")) { v.removeAttribute("src"); try { v.load(); } catch {} }
  $("#clip-bar").classList.add("hidden");
}

// ---------- wiring ----------
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setTab(t.dataset.tab)));
$("#clip-back").addEventListener("click", () => setTab("rec"));
$("#stage-close").addEventListener("click", closeStage);
$("#stage").addEventListener("click", (e) => { if (e.target.id === "stage") closeStage(); });
$("#zoom-reset").addEventListener("click", () => zoomer && zoomer.reset());
$("#sound-toggle").addEventListener("click", toggleSound);
$("#debug-toggle").addEventListener("click", () => $("#debug").classList.toggle("hidden"));
$("#debug-clear").addEventListener("click", () => { $("#debug-log").textContent = ""; });
$("#debug-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#debug-log").textContent); toast("Debug log copied"); } catch { toast("Copy failed"); }
});
$("#refresh").addEventListener("click", () => loadCameras(true));
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#stage").classList.contains("hidden")) closeStage(); });

loadConfig();
loadCameras(false);
setInterval(() => { if (!session) loadCameras(false); }, 30 * 60 * 1000);
