"use strict";
// Vicolocal dashboard. Browser does WebRTC directly with the camera over the LAN; the server
// only bridges signaling (see vico_server.py / vico_signaling.py).

const $ = (s) => document.querySelector(s);
const grid = $("#grid");
const LS = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
             set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };

// ---------- config (env-settable via /api/config) ----------
let CFG = { batteryWarn: true, simple: false, controls: true, archive: false };
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
  loadArchive();
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

// ---------- drag reorder ----------
// The tile lifts and follows the pointer, the other tiles slide aside to show where it will
// land, and nothing is placed until it's let go (Escape puts it back). A mouse lifts the tile
// as soon as it moves; touch, where a swipe has to scroll the page, presses and holds first,
// like the phone home screen.
const HOLD_MS = 450, MOVE_PX = 6, EDGE_PX = 80, SLIDE = { duration: 200, easing: "cubic-bezier(.2, .7, .3, 1)" };
const coarsePointer = matchMedia("(pointer: coarse)").matches;
$("#hint").innerHTML = (coarsePointer ? "Press and hold a tile to move it &middot; tap" : "Drag tiles to reorder &middot; click") + " to watch live";
let press = null;    // pointer down on a tile: {card, id, x, y, cx, cy, mouse, holdT}
let drag = null;     // tile lifted: {card, slot, w, h, ox, oy, home, slots, armed, raf}
let settling = false;

function attachDrag(card) {
  card.addEventListener("dragstart", (e) => e.preventDefault());  // no native text/img drag
  card.addEventListener("contextmenu", (e) => { if (press) e.preventDefault(); });  // Android long-press menu
  // once a tile is lifted, the finger moves it instead of scrolling the page
  card.addEventListener("touchmove", (e) => { if (drag) e.preventDefault(); }, { passive: false });
  card.addEventListener("pointerdown", (e) => {
    if ((e.button != null && e.button !== 0) || press || settling) return;   // left button / touch only
    press = { card, id: e.pointerId, x: e.clientX, y: e.clientY, cx: e.clientX, cy: e.clientY, mouse: e.pointerType === "mouse" };
    if (!press.mouse) press.holdT = setTimeout(liftTile, HOLD_MS);
    addEventListener("pointermove", onPressMove);
    addEventListener("pointerup", onPressEnd);
    addEventListener("pointercancel", onPressEnd);
  });
}

function onPressMove(e) {
  if (!press || press.id !== e.pointerId) return;
  press.cx = e.clientX; press.cy = e.clientY;
  if (!drag) {
    if (Math.hypot(e.clientX - press.x, e.clientY - press.y) <= MOVE_PX) return;  // still a tap
    if (!press.mouse) return endPress();   // moved before the hold: a scroll, not a drag
    liftTile();
  }
  e.preventDefault();
  followPointer();
}

function onPressEnd(e) {
  if (!press || press.id !== e.pointerId) return;
  const tap = !drag && e.type === "pointerup", card = press.card;
  endPress();
  if (tap) openCamera(card._cam);   // offline cameras open straight to Recordings
}

function endPress() {
  clearTimeout(press.holdT);
  removeEventListener("pointermove", onPressMove);
  removeEventListener("pointerup", onPressEnd);
  removeEventListener("pointercancel", onPressEnd);
  press = null;
  if (drag) dropTile();
}

// Pin the tile to the window where it was drawn, leaving a dashed slot in its grid cell.
function liftTile() {
  const card = press.card, r = card.getBoundingClientRect();
  const slot = document.createElement("div");
  slot.className = "card-slot";
  slot.style.height = r.height + "px";
  drag = { card, slot, w: r.width, h: r.height, ox: press.x - r.left, oy: press.y - r.top,
           home: card.nextElementSibling, armed: false, raf: 0 };
  grid.insertBefore(slot, card);
  Object.assign(card.style, { position: "fixed", left: "0", top: "0", margin: "0", width: r.width + "px", height: r.height + "px" });
  card.classList.add("lifted");
  document.documentElement.classList.add("tile-drag");
  measureSlots();
  followPointer();
  drag.raf = requestAnimationFrame(edgeScroll);
  try { navigator.vibrate(12); } catch {}
}

// Where each grid cell sits on the page. Read only while nothing is mid-slide, so a tile
// sliding past the lifted one can't trigger another move.
function measureSlots() {
  drag.slots = [...grid.children].filter((el) => el !== drag.card).map((el) => {
    const r = el.getBoundingClientRect();
    return { el, left: r.left + scrollX, top: r.top + scrollY, right: r.right + scrollX, bottom: r.bottom + scrollY };
  });
}

function followPointer() {
  const x = press.cx - drag.ox, y = press.cy - drag.oy;
  drag.card.style.transform = `translate3d(${x}px, ${y}px, 0) scale(1.03)`;
  // the lifted tile's centre picks the cell, not the pointer: a tile grabbed by its edge
  // shouldn't swap the moment the pointer crosses into the next one
  const cx = x + drag.w / 2 + scrollX, cy = y + drag.h / 2 + scrollY;
  const hit = drag.slots.find((s) => cx >= s.left && cx <= s.right && cy >= s.top && cy <= s.bottom);
  if (!hit || hit.el === drag.slot) return;
  const after = drag.slot.compareDocumentPosition(hit.el) & Node.DOCUMENT_POSITION_FOLLOWING;
  moveSlot(after ? hit.el.nextSibling : hit.el);
}

// Move the slot, sliding each tile it displaces from where it was drawn to its new cell.
function moveSlot(before) {
  const tiles = drag.slots.map((s) => s.el).filter((el) => el !== drag.slot);
  const from = tiles.map((el) => el.getBoundingClientRect());
  for (const el of tiles) for (const a of el.getAnimations()) a.cancel();
  grid.insertBefore(drag.slot, before);
  measureSlots();
  tiles.forEach((el, i) => {
    const to = el.getBoundingClientRect(), dx = from[i].left - to.left, dy = from[i].top - to.top;
    if (dx || dy) el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], SLIDE);
  });
}

// A phone shows only a few tiles: while the pointer rests near the top or bottom edge, scroll
// the page under the lifted tile. It arms once the pointer has been away from the edge, so
// lifting a tile that sits low on the screen doesn't set the page running.
function edgeScroll() {
  if (!drag) return;
  drag.raf = requestAnimationFrame(edgeScroll);
  const y = press.cy, h = innerHeight;
  const dir = y < EDGE_PX ? -1 : y > h - EDGE_PX ? 1 : 0;
  if (!dir) { drag.armed = true; return; }
  if (!drag.armed) return;
  const past = dir < 0 ? EDGE_PX - y : y - (h - EDGE_PX), before = scrollY;
  scrollBy(0, dir * Math.ceil(16 * Math.min(1, past / EDGE_PX)));
  if (scrollY !== before) followPointer();
}

// Glide the tile into its slot, then put it back in the grid there.
function dropTile() {
  const { card, slot } = drag, to = slot.getBoundingClientRect(), from = card.style.transform;
  cancelAnimationFrame(drag.raf);
  drag = null; settling = true;
  document.documentElement.classList.remove("tile-drag");
  card.style.transform = `translate3d(${to.left}px, ${to.top}px, 0)`;
  const glide = card.animate([{ transform: from }, { transform: card.style.transform }], SLIDE);
  glide.onfinish = glide.oncancel = () => {
    grid.insertBefore(card, slot);
    slot.remove();
    card.removeAttribute("style");
    card.classList.remove("lifted");
    settling = false;
    persistOrder();
  };
}

addEventListener("keydown", (e) => { if (e.key === "Escape" && drag) { moveSlot(drag.home); endPress(); } });

// ---------- zoom / pan on the video ----------
function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
function setupZoom(wrap, layers, badge) {
  let scale = 1, tx = 0, ty = 0;
  const pts = new Map(); let pinch = null, pan = null; const MAX = 6;
  function apply() {
    const mx = (scale - 1) * wrap.clientWidth / 2, my = (scale - 1) * wrap.clientHeight / 2;
    tx = Math.max(-mx, Math.min(mx, tx)); ty = Math.max(-my, Math.min(my, ty));
    for (const el of layers) el.style.transform = `translate(${tx}px,${ty}px) scale(${scale})`;
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

// ---------- camera commands (over the WebRTC data channel) ----------
// The app's PlayControl sends JSON commands straight to the camera on the data channel.
// When the camera's answer carries a version (every camera here), each one goes wrapped in an
// SCP frame: AB CD 18 00 | stream id (u16 BE) | 00 00 | length (u32 BE) | JSON. Replies come
// back the same way as {action, requestID, returnValue}: 0 = done.
const CMD_TIMEOUT_MS = 5000;
const utf8 = new TextEncoder();
let scpStream = Math.floor(Math.random() * 1000);
function scpFrame(json) {
  const body = utf8.encode(json), out = new Uint8Array(12 + body.length), v = new DataView(out.buffer);
  scpStream = (scpStream + 1) % 0xffff;
  v.setUint16(0, 0xabcd); v.setUint8(2, 0x18); v.setUint16(4, scpStream); v.setUint32(8, body.length);
  out.set(body, 12);
  return out;
}

// Fire one command; `params` is a JSON string (the camera wants floats written as floats).
function sendCmd(s, action, params) {
  if (!s || !s.dc || s.dc.readyState !== "open") return null;
  const now = Date.now(), id = `${now}-${Math.floor(Math.random() * 1000)}`;
  let json = JSON.stringify({ requestID: id, connectionID: "7893feb", timeStamp: Math.floor(now / 1000), action, targetId: s.serial });
  if (params) json = json.slice(0, -1) + ',"parameters":' + params + "}";
  try { s.dc.send(s.scp ? scpFrame(json) : json); } catch (e) { dbg("err", `${action}: ${e}`); return null; }
  dbg("dc", `→ ${action}${params ? " " + params : ""}`);
  return id;
}

// Send and wait for the camera's returnValue (-1 = not connected, -2 = no answer in time).
function camCmd(action, params) {
  const s = session;
  return new Promise((resolve) => {
    const id = sendCmd(s, action, params);
    if (!id) return resolve(-1);
    const t = setTimeout(() => { s.pending.delete(id); resolve(-2); }, CMD_TIMEOUT_MS);
    s.pending.set(id, (rv) => { clearTimeout(t); resolve(rv); });
  });
}

function onDcMessage(ev) {
  let text = ev.data;
  if (typeof text !== "string") {
    let b = new Uint8Array(text);
    if (b.length >= 12 && b[0] === 0xab && b[1] === 0xcd) b = b.subarray(12, 12 + new DataView(b.buffer, b.byteOffset).getUint32(8));
    text = new TextDecoder().decode(b);
  }
  let m;
  try { m = JSON.parse(text); } catch { dbg("dc", "← " + text.slice(0, 160)); return; }
  const rv = m.returnValue != null ? m.returnValue : m.returnVal;
  dbg("dc", `← ${m.action || "?"} ${rv != null ? rv : text.slice(0, 120)}`);
  const done = session && session.pending.get(m.requestID);
  if (done) { session.pending.delete(m.requestID); done(rv); }
}

// ---------- pan / tilt ----------
// "rotate" {pitch, yaw}, each -1..1, runs the motor briefly at that speed (pitch +1 tilts down,
// yaw +1 pans left); repeated every ~250 ms it keeps moving, as with the app's joystick. The
// next one goes out only once the camera has answered the last, so letting go stops it at once
// rather than leaving a backlog to run on. Replies: 1000-1007 = at the end of travel; for
// ~10 s after waking the motor refuses with 4 (2000 = calibrating).
const PTZ_DIRS = { up: [-1, 0], down: [1, 0], left: [0, 1], right: [0, -1] };
const KEY_DIRS = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ptzHold = null;

function ptzUsable() {
  return !!(CFG.controls !== false && currentCam && currentCam.canRotate && session && session.dc && session.dc.readyState === "open");
}

async function ptzStart(dir) {
  if (ptzHold || !ptzUsable()) return;
  const hold = ptzHold = { dir, steps: 0, warned: false };
  $(`.ptz-btn.${dir}`).classList.add("active");
  while (ptzHold === hold && session) {
    const [p, y] = PTZ_DIRS[dir], k = hold.steps ? 1 : 0.5, sent = Date.now();   // a tap nudges, a hold keeps going
    const rv = await camCmd("rotate", `{"pitch":${(p * k).toFixed(2)},"yaw":${(y * k).toFixed(2)}}`);
    if (ptzHold !== hold) break;   // let go, or the view closed, while the step was out
    if (rv === 0) hold.steps++;
    else if (rv === 4 || rv === 2000) {   // motor still starting up / calibrating
      if (!hold.warned) toast("The camera's motor is still waking up — try again in a few seconds");
      hold.warned = true;
      await sleep(800);
    } else if (rv >= 1000 && rv < 1008) {
      toast("The camera can't turn any further that way");
      break;
    } else {
      toast(rv === -2 ? "The camera didn't answer the move" : rv === -1 ? "Not connected to the camera" : `The camera refused to move (code ${rv})`);
      break;
    }
    await sleep(Math.max(0, 250 - (Date.now() - sent)));
  }
  if (ptzHold === hold) ptzStop();
}

function ptzStop() {
  ptzHold = null;
  document.querySelectorAll(".ptz-btn.active").forEach((b) => b.classList.remove("active"));
}

// ---------- white light ----------
// "setWhiteLight" {value: 2 on, 1 off}. It's a battery hog, so it goes off with the stream.
async function toggleLight() {
  const s = session, b = $("#light-btn");
  if (!s || b.classList.contains("busy")) return;
  const on = !s.lightOn;
  b.classList.add("busy");
  const rv = await camCmd("setWhiteLight", `{"value":${on ? 2 : 1}}`);
  b.classList.remove("busy");
  if (s !== session) return;
  if (rv === 0) { s.lightOn = on; toast(on ? "Light on — it turns off when you close the view" : "Light off"); }
  else toast(rv === -2 ? "The camera didn't answer the light switch" : `The camera refused the light (code ${rv})`);
  updateLiveBar();
}

// ---------- talk ----------
// The camera already gets an audio track from us (silence: it won't stream without one), so
// talking just swaps in the microphone, as the app's speakEnable() does. Tap to start and tap
// again to stop, or hold while speaking and let go. Browsers only allow the mic over HTTPS.
async function startTalk() {
  const s = session;
  if (!s || s.talking) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    toast("Talking needs the dashboard opened over HTTPS"); return;
  }
  s.talking = "starting"; updateLiveBar();
  try {
    const mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    if (s !== session || s.talking !== "starting") { mic.getTracks().forEach((t) => t.stop()); return; }
    await s.audioSender.replaceTrack(mic.getAudioTracks()[0]);
    s.mic = mic; s.talking = true;
    dbg("pc", "talking: microphone on");
  } catch (e) {
    s.talking = false;
    toast(e && e.name === "NotAllowedError" ? "Microphone permission was denied" : "Microphone unavailable: " + ((e && e.message) || e));
  }
  updateLiveBar();
}

function stopTalk(s = session) {
  if (!s || !s.talking) return;
  s.talking = false;
  if (s.mic) {
    s.audioSender.replaceTrack(s.silentTrack).catch(() => {});
    s.mic.getTracks().forEach((t) => t.stop());
    s.mic = null;
    dbg("pc", "talking: microphone off");
  }
  if (s === session) updateLiveBar();
}

// ---------- MagicPix ----------
// The app's MagicPix is a filter the phone runs on the decoded picture (nothing goes to the
// camera), so this is our own WebGL take on it: a few times a second it measures how dark the
// picture is and, only when it's dark, lifts the shadows with a gamma curve, softens the noise
// that brings up, and keeps the colours. It draws over the video and is remembered per camera.
const MP_VS = "attribute vec2 p; varying vec2 uv;" +
  "void main() { uv = vec2(p.x * .5 + .5, .5 - p.y * .5); gl_Position = vec4(p, 0., 1.); }";
const MP_FS = `precision mediump float;
varying vec2 uv;
uniform sampler2D tex;
uniform vec2 px;      // one texel
uniform float g;      // gamma; < 1 lifts the shadows
uniform float amt;    // 0..1, how hard we're boosting
void main() {
  vec3 c = texture2D(tex, uv).rgb;
  vec3 n = .25 * (texture2D(tex, uv + vec2(px.x, 0.)).rgb + texture2D(tex, uv - vec2(px.x, 0.)).rgb +
                  texture2D(tex, uv + vec2(0., px.y)).rgb + texture2D(tex, uv - vec2(0., px.y)).rgb);
  c = mix(c, n, .4 * amt);
  float y = max(dot(c, vec3(.299, .587, .114)), .001);
  float y2 = pow(y, g);
  y2 = mix(y2, y2 * y2 * (3. - 2. * y2), .2 * amt);
  vec3 keep = c * min(y2 / y, 8.);
  vec3 per = pow(c, vec3(g));
  gl_FragColor = vec4(clamp(mix(per, keep, .5), 0., 1.), 1.);
}`;
const MP = { gl: null, u: null, meter: null, on: false, raf: 0, vfc: 0, g: 1, gTarget: 1, meterAt: 0, drawn: false };

function mpSetup() {
  if (MP.gl) return true;
  const gl = $("#magic-canvas").getContext("webgl", { alpha: false, antialias: false });
  if (!gl) return false;
  const prog = gl.createProgram();
  for (const [type, src] of [[gl.VERTEX_SHADER, MP_VS], [gl.FRAGMENT_SHADER, MP_FS]]) {
    const sh = gl.createShader(type); gl.shaderSource(sh, src); gl.compileShader(sh); gl.attachShader(prog, sh);
  }
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { dbg("err", "MagicPix: " + gl.getProgramInfoLog(prog)); return false; }
  gl.useProgram(prog);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const p = gl.getAttribLocation(prog, "p");
  gl.enableVertexAttribArray(p); gl.vertexAttribPointer(p, 2, gl.FLOAT, false, 0, 0);
  gl.bindTexture(gl.TEXTURE_2D, gl.createTexture());
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  MP.u = { px: gl.getUniformLocation(prog, "px"), g: gl.getUniformLocation(prog, "g"), amt: gl.getUniformLocation(prog, "amt") };
  const m = document.createElement("canvas"); m.width = 32; m.height = 18;
  MP.meter = m.getContext("2d", { willReadFrequently: true });
  MP.gl = gl;
  return true;
}

// Mean brightness of a 32x18 thumbnail of the picture -> the gamma that lifts it to ~0.4.
function mpMeasure(v) {
  MP.meter.drawImage(v, 0, 0, 32, 18);
  const d = MP.meter.getImageData(0, 0, 32, 18).data;
  let sum = 0;
  for (let i = 0; i < d.length; i += 4) sum += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
  const mean = sum / (d.length / 4) / 255;
  MP.gTarget = mean >= 0.4 ? 1 : Math.max(0.38, Math.log(0.4) / Math.log(Math.max(mean, 0.01)));
}

function mpFrame() {
  MP.raf = MP.vfc = 0;
  if (!MP.on) return;
  const v = $("#video"), gl = MP.gl, c = gl.canvas;
  if (v.videoWidth && v.readyState >= 2) {
    if (c.width !== v.videoWidth || c.height !== v.videoHeight) {
      c.width = v.videoWidth; c.height = v.videoHeight;
      gl.viewport(0, 0, c.width, c.height); gl.uniform2f(MP.u.px, 1 / c.width, 1 / c.height);
    }
    const now = performance.now();
    if (now - MP.meterAt > 400) { MP.meterAt = now; try { mpMeasure(v); } catch {} }
    MP.g += (MP.gTarget - MP.g) * 0.1;   // ease, so the picture doesn't pump
    gl.uniform1f(MP.u.g, MP.g);
    gl.uniform1f(MP.u.amt, Math.min(1, Math.max(0, (1 - MP.g) / 0.6)));
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, v);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    if (!MP.drawn) { MP.drawn = true; c.classList.remove("hidden"); }
    $("#magic-btn").classList.toggle("boosting", MP.g < 0.93);
  }
  if (v.requestVideoFrameCallback) MP.vfc = v.requestVideoFrameCallback(mpFrame);
  else MP.raf = requestAnimationFrame(mpFrame);
}

function mpStart() {
  if (MP.on) return true;
  if (!mpSetup()) return false;
  Object.assign(MP, { on: true, g: 1, gTarget: 1, meterAt: 0, drawn: false });
  mpFrame();
  return true;
}

function mpStop() {
  const v = $("#video");
  MP.on = false;
  if (MP.vfc && v.cancelVideoFrameCallback) v.cancelVideoFrameCallback(MP.vfc);
  if (MP.raf) cancelAnimationFrame(MP.raf);
  MP.vfc = MP.raf = 0;
  $("#magic-canvas").classList.add("hidden");
  $("#magic-btn").classList.remove("boosting");
}

function magicPref(serial) { return LS.get("vmagic:" + serial) === "1"; }
function applyMagic(cam) {
  if (!cam || !magicPref(cam.serial)) mpStop();
  else if (!mpStart()) { LS.set("vmagic:" + cam.serial, "0"); toast("MagicPix needs WebGL, which this browser doesn't have"); }
  updateLiveBar();
}
function toggleMagic() {
  if (!currentCam) return;
  const on = !magicPref(currentCam.serial);
  LS.set("vmagic:" + currentCam.serial, on ? "1" : "0");
  applyMagic(currentCam);
  if (MP.on === on) toast(on ? "MagicPix on — dark pictures get brightened" : "MagicPix off");
}

// ---------- live controls bar ----------
function updateLiveBar() {
  const s = session, cam = currentCam, ctl = !!(s && cam && CFG.controls !== false);
  const ready = !!(s && s.dc && s.dc.readyState === "open");
  const talk = $("#talk-btn"), light = $("#light-btn"), move = $("#move-btn");
  $("#live-bar").classList.toggle("hidden", !s);
  talk.classList.toggle("hidden", !ctl);
  light.classList.toggle("hidden", !(ctl && cam.whiteLight));
  move.classList.toggle("hidden", !(ctl && cam.canRotate));
  talk.disabled = light.disabled = move.disabled = !ready;
  talk.classList.toggle("on", !!(s && s.talking));
  talk.querySelector(".lbl").textContent = s && s.talking === true ? "Talking… tap to stop" : s && s.talking ? "Starting mic…" : "Talk";
  light.classList.toggle("on", !!(s && s.lightOn));
  light.querySelector(".lbl").textContent = s && s.lightOn ? "Light on" : "Light";
  $("#magic-btn").classList.toggle("on", MP.on);
  const pad = ready && ctl && cam.canRotate && LS.get("vptz") !== "0";
  move.classList.toggle("on", pad);
  $("#ptz").classList.toggle("hidden", !pad);
}

let currentCam = null, hls = null;

function openCamera(cam) {
  closeStream(); stopClip();
  currentCam = cam;
  $("#stage").classList.remove("hidden");
  $("#stage-name").textContent = cam.name;
  $(".stage-tabs").classList.remove("hidden"); $("#arc-bar").classList.add("hidden");
  if (!zoomer) zoomer = setupZoom($("#video-wrap"), [$("#video"), $("#magic-canvas")], $("#zoom-badge"));
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
  session = { ws, pc, audioCtx, statsTimer: null, serial: cam.serial, scp: false, pending: new Map(),
              dc: null, audioSender: null, silentTrack: null, talking: false, mic: null, lightOn: false };
  updateLiveBar();

  try {   // camera needs a sendrecv audio track with a real ssrc -> feed it silence
    if (audioCtx.state === "suspended") audioCtx.resume();   // autoplay policy (Fire/mobile)
    const dst = audioCtx.createMediaStreamDestination();
    const osc = audioCtx.createOscillator(), g = audioCtx.createGain();
    g.gain.value = 0; osc.connect(g); g.connect(dst); osc.start();
    session.silentTrack = dst.stream.getAudioTracks()[0];
    session.audioSender = pc.addTrack(session.silentTrack, dst.stream);   // talking swaps the mic in
  } catch (e) { dbg("err", "silent audio failed: " + e); }
  pc.addTransceiver("video", { direction: "recvonly" });
  const dc = session.dc = pc.createDataChannel("dataChannel");   // camera commands (light, pan/tilt)
  dc.binaryType = "arraybuffer";
  dc.onopen = () => { dbg("dc", "data channel open"); updateLiveBar(); };
  dc.onclose = () => { dbg("dc", "data channel closed"); ptzStop(); updateLiveBar(); };
  dc.onmessage = onDcMessage;

  pc.ontrack = (e) => {
    const vid = $("#video");
    // Collect audio + video into one stream we control, so sound plays when unmuted.
    if (!session.remoteStream) session.remoteStream = new MediaStream();
    session.remoteStream.addTrack(e.track);
    vid.srcObject = session.remoteStream;
    dbg("pc", "track " + e.track.kind);
    if (e.track.kind === "video") {
      // the track arrives with the answer, before the connection is up: "live" waits for both
      session.gotVideo = true;
      $("#spinner").classList.add("hidden");
      if (pc.connectionState === "connected") setState("live", true);
      applySound(currentCam);            // honor this camera's saved sound setting
      applyMagic(currentCam);            // and its MagicPix setting
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
    else if (session && session.gotVideo) setState("live", true);
    if (["failed", "disconnected", "closed"].includes(pc.connectionState)) $("#spinner").classList.add("hidden");
  };

  ws.onmessage = async (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "ready") {
      setState("negotiating…", false);
      await pc.setLocalDescription(await pc.createOffer());
      ws.send(JSON.stringify({ type: "offer", sdp: pc.localDescription.sdp })); dbg("pc", "sent offer");
    } else if (m.type === "answer") {
      session.scp = !!m.scp;
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
  const s = session;
  session = null;
  ptzStop(); mpStop(); stopTalk(s);
  clearInterval(s.statsTimer);
  for (const done of s.pending.values()) done(-1);
  s.ws.onmessage = s.pc.ontrack = s.pc.onconnectionstatechange = s.pc.oniceconnectionstatechange = null;
  // the light is a battery hog: switch it off before hanging up (as the app does), and give
  // the data channel a moment to deliver that before the connection goes
  const lightOff = s.lightOn && sendCmd(s, "setWhiteLight", '{"value":1}');
  const hangUp = () => {
    try { if (s.ws.readyState === 1) s.ws.send(JSON.stringify({ type: "bye" })); } catch {}
    try { s.ws.close(); } catch {}
    try { s.pc.close(); } catch {}
    try { s.audioCtx.close(); } catch {}
  };
  if (lightOff) setTimeout(hangUp, 500); else hangUp();
  $("#video").srcObject = null;
  updateLiveBar();
}
function closeStage() { exitFullscreen(); closeStream(); stopClip(); $("#stage").classList.add("hidden"); }

// ---------- fullscreen ----------
// The video box goes fullscreen (zoom and pan keep working) where the Fullscreen API exists;
// iPhone Safari only lets the <video> itself go fullscreen, in its native player.
const fsElement = () => document.fullscreenElement || document.webkitFullscreenElement;
function exitFullscreen() {
  if (!fsElement()) return;
  try { (document.exitFullscreen || document.webkitExitFullscreen).call(document); } catch {}
}
function toggleFullscreen() {
  if (fsElement()) return exitFullscreen();
  const wrap = $("#video-wrap"), vid = $("#video");
  if (wrap.classList.contains("hidden")) return;           // recordings list showing, no video
  const req = wrap.requestFullscreen || wrap.webkitRequestFullscreen;
  if (req && (document.fullscreenEnabled || document.webkitFullscreenEnabled)) {
    Promise.resolve(req.call(wrap))
      .then(() => { try { screen.orientation.lock("landscape").catch(() => {}); } catch {} })
      .catch((e) => toast("Fullscreen failed: " + e.message));
  } else if (vid.webkitEnterFullscreen) {
    try { vid.webkitEnterFullscreen(); } catch { toast("Fullscreen isn't available here"); }
  } else toast("Fullscreen isn't available in this browser");
}
const onFsChange = () => { if (!fsElement()) { try { screen.orientation.unlock(); } catch {} } };
document.addEventListener("fullscreenchange", onFsChange);
document.addEventListener("webkitfullscreenchange", onFsChange);

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
  for (const c of clips) list.appendChild(clipTile(c, c.thumb, `${c.duration}s`, () => playClip(c)));
}

// One tile in a clip grid (cloud recordings and the archive share it).
function clipTile(c, thumb, badge, onClick) {
  const el = document.createElement("div"); el.className = "clip";
  const t = new Date(c.time * 1000);
  el.innerHTML = `
    <div class="cthumb" ${thumb ? `style="background-image:url('${thumb}')"` : ""}>
      ${thumb ? "" : '<div class="cnoimg">&#x1f39e;&#xfe0f;</div>'}
      <div class="cplay">&#x25b6;</div>
      <div class="cdur">${esc(badge)}</div>
    </div>
    <div class="cmeta">
      <div class="ctime">${t.toLocaleDateString([], { month: "short", day: "numeric" })} ${t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</div>
      ${c.tags && c.tags.length ? `<div class="ctags">${esc(c.tags.join(", "))}</div>` : ""}
    </div>`;
  el.addEventListener("click", onClick);
  return el;
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
  $("#clip-dl").download = "";
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

// ---------- local archive ----------
// Clips the server keeps on disk (VICO_ARCHIVE_DIR, filled by vico_pull.py) outlive the cloud's
// few days. They open from the header button, which only shows when there is something in it,
// in the same stage as a camera but without the Live/Recordings tabs; a month filter narrows
// the list. Playback is a plain mp4 from this server, no cloud involved.
let archive = [];
async function loadArchive() {
  if (!CFG.archive) return;
  try { archive = await (await fetch("/api/archive")).json(); } catch { archive = []; }
  if (!Array.isArray(archive)) archive = [];
  $("#archive-btn").classList.toggle("hidden", !archive.length);
}
const archiveUrl = (file) => "/api/archive/file?path=" + encodeURIComponent(file);
const monthOf = (c) => { const d = new Date(c.time * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; };
function fmtSize(b) { return b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : b >= 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.round(b / 1e3) + " kB"; }

function openArchive() {
  closeStream(); stopClip();
  currentCam = null;
  $("#stage").classList.remove("hidden");
  $("#stage-name").textContent = "Archive";
  $(".stage-tabs").classList.add("hidden");
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  if (!zoomer) zoomer = setupZoom($("#video-wrap"), [$("#video"), $("#magic-canvas")], $("#zoom-badge"));
  updateSoundBtn(false, false); updateLiveBar();
  $("#battery-warn").classList.add("hidden");
  const sel = $("#arc-month"), months = [...new Set(archive.map(monthOf))];
  sel.innerHTML = `<option value="">All months</option>` + months.map((m) => {
    const d = new Date(m + "-15T12:00:00");
    return `<option value="${m}">${d.toLocaleDateString([], { month: "short", year: "numeric" })}</option>`;
  }).join("");
  showArchiveList();
}

function showArchiveList() {
  stopClip();
  $("#video-wrap").classList.add("hidden");
  $("#rec-panel").classList.remove("hidden"); $("#arc-bar").classList.remove("hidden");
  setState("archive", false);
  const month = $("#arc-month").value, list = $("#rec-list");
  const clips = month ? archive.filter((c) => monthOf(c) === month) : archive;
  $("#arc-count").textContent = `${clips.length} clip${clips.length === 1 ? "" : "s"}`;
  list.innerHTML = "";
  if (!clips.length) { list.innerHTML = '<div class="rec-empty">nothing archived yet</div>'; return; }
  for (const c of clips) list.appendChild(clipTile(c, c.thumb && archiveUrl(c.thumb), fmtSize(c.size), () => playArchived(c)));
}

function playArchived(c) {
  stopClip();
  $("#rec-panel").classList.add("hidden");
  $("#video-wrap").classList.remove("hidden");
  $("#clip-bar").classList.remove("hidden");
  $("#spinner").classList.add("hidden");
  zoomer && zoomer.reset();
  const v = $("#video"), url = archiveUrl(c.file);
  v.srcObject = null; v.muted = false; v.controls = true;
  const t = new Date(c.time * 1000);
  $("#clip-meta").textContent = `${t.toLocaleString()} · ${fmtSize(c.size)}`;
  $("#clip-dl").href = url; $("#clip-dl").download = c.file.split("/").pop();
  v.src = url; v.play().catch(() => {});
}

// ---------- wiring ----------
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setTab(t.dataset.tab)));
$("#clip-back").addEventListener("click", () => (currentCam ? setTab("rec") : showArchiveList()));
$("#archive-btn").addEventListener("click", openArchive);
$("#arc-month").addEventListener("change", showArchiveList);
$("#stage-close").addEventListener("click", closeStage);
$("#stage").addEventListener("click", (e) => { if (e.target.id === "stage") closeStage(); });
$("#fullscreen").addEventListener("click", toggleFullscreen);
// controls inside the video box: keep their taps away from the box's pan/zoom handlers
for (const id of ["#fs-exit", "#zoom-badge"]) $(id).addEventListener("pointerdown", (e) => e.stopPropagation());
$("#fs-exit").addEventListener("click", exitFullscreen);
$("#zoom-badge").addEventListener("click", () => zoomer && zoomer.reset());
$("#sound-toggle").addEventListener("click", toggleSound);
$("#debug-toggle").addEventListener("click", () => $("#debug").classList.toggle("hidden"));
$("#debug-clear").addEventListener("click", () => { $("#debug-log").textContent = ""; });
$("#debug-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#debug-log").textContent); toast("Debug log copied"); } catch { toast("Copy failed"); }
});
$("#refresh").addEventListener("click", () => { loadCameras(true); loadArchive(); });

// live controls
$("#light-btn").addEventListener("click", toggleLight);
$("#magic-btn").addEventListener("click", toggleMagic);
$("#move-btn").addEventListener("click", () => { LS.set("vptz", LS.get("vptz") === "0" ? "1" : "0"); updateLiveBar(); });
const ptzPad = $("#ptz");
ptzPad.addEventListener("pointerdown", (e) => e.stopPropagation());   // not the video's zoom/pan
ptzPad.addEventListener("dblclick", (e) => e.stopPropagation());
ptzPad.querySelectorAll(".ptz-btn").forEach((b) => {
  b.addEventListener("pointerdown", (e) => {
    if (e.button != null && e.button !== 0) return;
    e.preventDefault();
    try { b.setPointerCapture(e.pointerId); } catch {}
    ptzStart(b.dataset.dir);
  });
  b.addEventListener("pointerup", ptzStop);
  b.addEventListener("pointercancel", ptzStop);
  b.addEventListener("contextmenu", (e) => e.preventDefault());
});
document.addEventListener("keydown", (e) => {
  const dir = KEY_DIRS[e.key];
  if (!dir || e.repeat || $("#stage").classList.contains("hidden") || !ptzUsable()) return;
  e.preventDefault();
  ptzStart(dir);
});
document.addEventListener("keyup", (e) => { if (ptzHold && ptzHold.dir === KEY_DIRS[e.key]) ptzStop(); });
// talk: a tap leaves the mic on (tap again to stop); a press held past the first moment
// talks only while held. A permission prompt on the first press counts as a tap.
const talkBtn = $("#talk-btn");
let talkPress = null;
talkBtn.addEventListener("pointerdown", (e) => {
  if ((e.button != null && e.button !== 0) || talkBtn.disabled) return;
  e.preventDefault();
  try { talkBtn.setPointerCapture(e.pointerId); } catch {}
  const was = !!(session && session.talking);
  talkPress = { was, hold: false };
  talkPress.t = setTimeout(() => { if (talkPress) talkPress.hold = !!(session && session.talking === true); }, 450);
  if (!was) startTalk();
});
const talkRelease = () => {
  if (!talkPress) return;
  clearTimeout(talkPress.t);
  if (talkPress.was || talkPress.hold) stopTalk();
  talkPress = null;
};
talkBtn.addEventListener("pointerup", talkRelease);
talkBtn.addEventListener("pointercancel", talkRelease);
talkBtn.addEventListener("contextmenu", (e) => e.preventDefault());
talkBtn.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  e.preventDefault();
  if (session && session.talking) stopTalk(); else startTalk();
});
// leaving the page with the light on: switch it off on the way out
addEventListener("pagehide", () => { if (session && session.lightOn) sendCmd(session, "setWhiteLight", '{"value":1}'); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#stage").classList.contains("hidden")) closeStage(); });

loadConfig();
loadCameras(false);
setInterval(() => { if (!session && !press && !settling) loadCameras(false); }, 30 * 60 * 1000);
