import { RobotStream } from './webrtc.js';
import { AntennaPad } from './antennas.js';

// ---- Tuning (radians, meters, seconds) --------------------------------------
// Conservative on purpose. Single-axis extremes were measured on a Lite, but
// combining them (e.g. full yaw + full pitch + raised head) can drive the
// Stewart platform into a pose that strains its motors. So the head works
// inside an ellipse in yaw/pitch that shrinks as the head moves up or down.
const MOUSE_SENS = 0.0022; // rad per pixel
const YAW_REL_LIMIT = 0.7; // head yaw relative to the body (~40°)
const PITCH_LIMIT = 0.35; // ~20°, + looks down
const Z_MIN = -0.02;
const Z_MAX = 0.012;
const Z_SHRINK = 0.4; // at the height limits, yaw/pitch range shrinks by 40%
const Z_RATE = 0.04; // m/s while Space/C is held
const BODY_LIMIT = 2.6; // ~149°, hardware stops at ~160°
const BODY_RATE = 1.4; // rad/s while Q/E is held
const ANTENNA_LIMIT = 2.8;
// The daemon's neutral: ~10° off vertical, because antennas held exactly
// upright tend to shake.
const ANTENNA_NEUTRAL = [-0.1745, 0.1745];

// Keep turning into the base limit for this much extra rotation and the base
// unwinds the long way round to the other side, like a turret wrapping.
const WRAP_PUSH = 0.6;

// Max speed the robot is ever commanded to move at. Input can jump (mouse
// flicks, recenter, seeding), the output always glides.
const SLEW = { yawRel: 3, pitch: 2.5, z: 0.05, body: 1.8, antenna: 4 };

// An antenna that sits this far from its settled command for this long is
// pushing against something (the head, the other antenna, a finger), so the
// command backs off to where it actually is.
const ANTENNA_STALL_ERR = 0.3; // rad
const ANTENNA_STALL_MS = 400;

const TOUCH_SENS = 0.005; // rad per CSS pixel dragged on the video
const TICK_MS = 20;
const deg = (r) => Math.round((r * 180) / Math.PI);

const $ = (id) => document.getElementById(id);
const video = $('video');

// ---- Pose: `pose` is what the controls ask for, `out` is what we send -------
const pose = { yawRel: 0, pitch: 0, z: 0, body: 0, antennas: [0, 0] };
const out = { yawRel: 0, pitch: 0, z: 0, body: 0, antennas: [0, 0] };
let dirty = false;
let controlling = false;
let push = 0; // accumulated rotation pushed into the base limit
let wrapping = false;

// Rotate the base by `delta`, spilling anything past the limit into `push`.
function turnBody(delta) {
  if (wrapping) return; // ride out the unwind before accepting more turn
  const next = pose.body + delta;
  if (Math.abs(next) <= BODY_LIMIT) {
    pose.body = next;
    push = 0;
    return;
  }
  const edge = Math.sign(next) * BODY_LIMIT;
  const over = next - edge;
  pose.body = edge;
  push = Math.sign(push) === Math.sign(over) ? push + over : over;
  if (Math.abs(push) >= WRAP_PUSH) {
    // Wrap to the far side, keeping roughly the same heading beyond the dead zone.
    pose.body = -edge;
    pose.yawRel = 0;
    push = 0;
    wrapping = true;
    showNotice('Unwinding base…');
  }
}

function clampPose() {
  // Mouse yaw past the neck limit turns the base, so you can keep turning
  // like in a game.
  const zNow = clamp(pose.z, Z_MIN, Z_MAX);
  const yawEdge = YAW_REL_LIMIT * (1 - Z_SHRINK * (zNow >= 0 ? zNow / Z_MAX : zNow / Z_MIN));
  if (Math.abs(pose.yawRel) > yawEdge) {
    const edge = Math.sign(pose.yawRel) * yawEdge;
    const spill = pose.yawRel - edge;
    pose.yawRel = edge;
    turnBody(spill);
  }
  pose.z = clamp(pose.z, Z_MIN, Z_MAX);
  // Keep (yaw, pitch) inside an ellipse scaled down away from neutral height.
  const zFrac = pose.z >= 0 ? pose.z / Z_MAX : pose.z / Z_MIN;
  const scale = 1 - Z_SHRINK * zFrac;
  const yawLim = YAW_REL_LIMIT * scale;
  const pitchLim = PITCH_LIMIT * scale;
  pose.pitch = clamp(pose.pitch, -pitchLim, pitchLim);
  const r = Math.hypot(pose.yawRel / yawLim, pose.pitch / pitchLim);
  if (r > 1) {
    // Pitch gives way first so mouse yaw still reaches the base.
    const maxPitch = pitchLim * Math.sqrt(Math.max(0, 1 - Math.min(1, (pose.yawRel / yawLim) ** 2)));
    pose.pitch = clamp(pose.pitch, -maxPitch, maxPitch);
    pose.yawRel = clamp(pose.yawRel, -yawLim, yawLim);
  }
  pose.body = clamp(pose.body, -BODY_LIMIT, BODY_LIMIT);
  pose.antennas = pose.antennas.map((a) => clamp(a, -ANTENNA_LIMIT, ANTENNA_LIMIT));
}

// Move `out` toward `pose` at bounded speed. Returns true if anything moved.
function slew(dt) {
  let moved = false;
  const step = (cur, want, rate) => {
    const next = cur + clamp(want - cur, -rate * dt, rate * dt);
    if (Math.abs(next - cur) > 1e-6) moved = true;
    return next;
  };
  out.yawRel = step(out.yawRel, pose.yawRel, SLEW.yawRel);
  out.pitch = step(out.pitch, pose.pitch, SLEW.pitch);
  out.z = step(out.z, pose.z, SLEW.z);
  out.body = step(out.body, pose.body, SLEW.body);
  out.antennas = out.antennas.map((a, i) => step(a, pose.antennas[i], SLEW.antenna));
  if (wrapping && Math.abs(out.body - pose.body) < 0.02) {
    wrapping = false;
    hideNotice();
  }
  return moved;
}

function target() {
  return {
    // Head orientation is in the world frame: add the body yaw so the head
    // turns with the base.
    target_head_pose: { x: 0, y: 0, z: out.z, roll: 0, pitch: out.pitch, yaw: out.body + out.yawRel },
    target_antennas: out.antennas,
    target_body_yaw: out.body,
  };
}

function seedFromState(s) {
  pose.body = s.body_yaw ?? 0;
  pose.yawRel = (s.head_pose?.yaw ?? 0) - pose.body;
  pose.pitch = s.head_pose?.pitch ?? 0;
  pose.z = s.head_pose?.z ?? 0;
  pose.antennas = s.antennas_position ? [...s.antennas_position] : [0, 0];
  // Start `out` at the real pose; if that was outside our soft limits, the
  // slew brings it back in gently.
  Object.assign(out, { ...pose, antennas: [...pose.antennas] });
  wrapping = false;
  push = 0;
  hideNotice();
  clampPose();
  antennaPad.setCommanded(pose.antennas);
  renderTelemetry();
}

let noticeTimer = null;
function showNotice(text) {
  clearTimeout(noticeTimer);
  $('notice').textContent = text;
  $('notice').hidden = false;
}
function hideNotice() {
  noticeTimer = setTimeout(() => ($('notice').hidden = true), 300);
}
function flashNotice(text) {
  if (wrapping) return; // don't hide the unwind notice
  showNotice(text);
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => ($('notice').hidden = true), 1500);
}

async function reseed() {
  try {
    const res = await fetch('/api/state/full');
    if (res.ok) seedFromState(await res.json());
  } catch (e) {
    console.warn('state fetch failed', e);
  }
}

function renderTelemetry() {
  $('t-yaw').textContent = deg(out.yawRel);
  $('t-pitch').textContent = deg(-out.pitch);
  $('t-z').textContent = Math.round(out.z * 1000);
  $('t-body').textContent = deg(out.body);
}

// ---- Status UI ---------------------------------------------------------------
function setStatus(name, cls, text) {
  $(`dot-${name}`).className = `dot ${cls}`;
  $(`st-${name}`).textContent = text;
}

// ---- WebSocket with reconnect ------------------------------------------------
function wsURL(path) {
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`;
}

class Link {
  constructor(path, { onOpen, onMessage, onClose }) {
    this.path = path;
    Object.assign(this, { onOpen, onMessage, onClose });
    this.connect();
  }
  connect() {
    const ws = new WebSocket(wsURL(this.path));
    this.ws = ws;
    ws.onopen = () => this.onOpen?.();
    ws.onmessage = (e) => this.onMessage?.(e.data);
    ws.onclose = () => {
      this.onClose?.();
      setTimeout(() => this.connect(), 1500);
    };
  }
  send(obj) {
    if (this.ws.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(obj));
    return true;
  }
}

const motion = new Link('/api/move/ws/set_target', {
  onOpen: () => {
    setStatus('daemon', 'ok', 'ready');
    $('banner').hidden = true;
    reseed();
  },
  onMessage: (data) => {
    const msg = JSON.parse(data);
    if (msg.status === 'error') console.warn('set_target rejected:', msg.detail);
  },
  onClose: () => explainDaemon(),
});

// When the motion socket is refused, ask the daemon why (e.g. the motor
// backend stopped) instead of just saying "offline".
async function explainDaemon() {
  try {
    const res = await fetch('/api/daemon/status');
    if (!res.ok) throw new Error(res.statusText);
    const s = await res.json();
    if (s.error) {
      setStatus('daemon', 'bad', 'error');
      $('st-daemon').title = s.error;
      showBanner(`Daemon: ${s.error} Restart the daemon once fixed.`);
      return;
    }
    setStatus('daemon', 'warn', s.state || 'unavailable');
  } catch {
    setStatus('daemon', 'bad', 'daemon offline');
  }
}

function showBanner(text) {
  const el = $('banner');
  if (el.textContent !== text) el.textContent = text;
  el.hidden = false;
}

let motorMode = null;
new Link('/api/state/ws/full?frequency=15', {
  onMessage: (data) => {
    const s = JSON.parse(data);
    if (s.antennas_position) {
      antennaPad.setActual(s.antennas_position);
      checkAntennaStall(s.antennas_position);
    }
    if (s.control_mode && s.control_mode !== motorMode) {
      motorMode = s.control_mode;
      if (motion.ws.readyState === WebSocket.OPEN) {
        setStatus('daemon', motorMode === 'enabled' ? 'ok' : 'warn', motorMode === 'enabled' ? 'ready' : `motors ${motorMode}`);
      }
    }
  },
});

const antennaStallSince = [0, 0];
function checkAntennaStall(actual) {
  if (!controlling) return;
  const now = performance.now();
  let backedOff = false;
  for (let i = 0; i < 2; i++) {
    const settled = Math.abs(out.antennas[i] - pose.antennas[i]) < 1e-3;
    if (!settled || Math.abs(out.antennas[i] - actual[i]) < ANTENNA_STALL_ERR) {
      antennaStallSince[i] = 0;
      continue;
    }
    if (!antennaStallSince[i]) {
      antennaStallSince[i] = now;
    } else if (now - antennaStallSince[i] > ANTENNA_STALL_MS) {
      pose.antennas[i] = out.antennas[i] = actual[i];
      antennaStallSince[i] = 0;
      backedOff = true;
    }
  }
  if (backedOff) {
    pose.antennas = [...pose.antennas];
    antennaPad.setCommanded(pose.antennas);
    dirty = true;
    flashNotice('Antenna blocked, backed off');
  }
}

// ---- Antenna pane --------------------------------------------------------------
const antennaPad = new AntennaPad($('antenna-canvas'), {
  onChange: (angles) => {
    pose.antennas = angles;
    dirty = true;
  },
});
$('ant-mirror').addEventListener('change', (e) => antennaPad.setMirror(e.target.checked));
$('ant-reset').addEventListener('click', () => {
  pose.antennas = [...ANTENNA_NEUTRAL];
  antennaPad.setCommanded(pose.antennas);
  dirty = true;
});

// ---- Input -------------------------------------------------------------------
const held = new Set();
const HELD_KEYS = new Set(['KeyQ', 'KeyE', 'Space', 'KeyC']);

document.addEventListener('keydown', (e) => {
  if (!controlling || e.target instanceof HTMLInputElement) return;
  if (HELD_KEYS.has(e.code)) {
    held.add(e.code);
    e.preventDefault();
  } else if (e.code === 'KeyM' && !e.repeat) {
    toggleVoice();
  } else if (e.code === 'KeyR' && !e.repeat) {
    recenter();
  }
});

function recenter() {
  Object.assign(pose, { yawRel: 0, pitch: 0, z: 0, body: 0 });
  push = 0;
  dirty = true;
}
document.addEventListener('keyup', (e) => held.delete(e.code));
// Never leave the base spinning because a keyup went to another window.
window.addEventListener('blur', () => held.clear());
document.addEventListener('visibilitychange', () => document.hidden && held.clear());

document.addEventListener('mousemove', (e) => {
  if (document.pointerLockElement !== video) return;
  pose.yawRel -= e.movementX * MOUSE_SENS;
  pose.pitch += e.movementY * MOUSE_SENS; // mouse up looks up
  dirty = true;
});

function lockPointer() {
  if (document.body.classList.contains('touch') || !video.requestPointerLock) return;
  // Returns a promise in Chromium; rejects e.g. when the page lacks focus.
  video.requestPointerLock()?.catch?.((e) => console.warn('pointer lock:', e.message));
}
video.addEventListener('click', () => controlling && lockPointer());
document.addEventListener('pointerlockchange', () => {
  document.body.classList.toggle('locked', document.pointerLockElement === video);
});

// ---- Touch: drag the video to look, joystick to spin/rise ------------------------
function enableTouchUI() {
  document.body.classList.add('touch');
}
if (matchMedia('(pointer: coarse)').matches) enableTouchUI();
window.addEventListener('pointerdown', (e) => e.pointerType === 'touch' && enableTouchUI(), { capture: true });

const lookDrags = new Map();
video.addEventListener('pointerdown', (e) => {
  if (e.pointerType === 'mouse' || !controlling) return;
  lookDrags.set(e.pointerId, { x: e.clientX, y: e.clientY });
  video.setPointerCapture(e.pointerId);
});
video.addEventListener('pointermove', (e) => {
  const last = lookDrags.get(e.pointerId);
  if (!last) return;
  pose.yawRel -= (e.clientX - last.x) * TOUCH_SENS;
  pose.pitch += (e.clientY - last.y) * TOUCH_SENS;
  last.x = e.clientX;
  last.y = e.clientY;
  dirty = true;
});
for (const type of ['pointerup', 'pointercancel']) {
  video.addEventListener(type, (e) => lookDrags.delete(e.pointerId));
}

// Analog stick: x spins the base, y raises/lowers the head. Values in [-1, 1].
const stick = { x: 0, y: 0, pointer: null };
const stickEl = $('stick');
const knob = stickEl.querySelector('.knob');
function updateStick(e) {
  const r = stickEl.getBoundingClientRect();
  const radius = r.width / 2;
  let dx = (e.clientX - (r.left + radius)) / radius;
  let dy = (e.clientY - (r.top + radius)) / radius;
  const mag = Math.hypot(dx, dy);
  if (mag > 1) {
    dx /= mag;
    dy /= mag;
  }
  const travel = radius - knob.offsetWidth / 2;
  knob.style.transform = `translate(${dx * travel}px, ${dy * travel}px)`;
  const DEAD = 0.2;
  const shape = (v) => (Math.abs(v) < DEAD ? 0 : (v - Math.sign(v) * DEAD) / (1 - DEAD));
  stick.x = shape(dx);
  stick.y = shape(dy);
}
function releaseStick() {
  stick.pointer = null;
  stick.x = stick.y = 0;
  stickEl.classList.remove('active');
  knob.style.transform = '';
}
stickEl.addEventListener('pointerdown', (e) => {
  stick.pointer = e.pointerId;
  stickEl.setPointerCapture(e.pointerId);
  stickEl.classList.add('active');
  updateStick(e);
});
stickEl.addEventListener('pointermove', (e) => e.pointerId === stick.pointer && updateStick(e));
stickEl.addEventListener('pointerup', releaseStick);
stickEl.addEventListener('pointercancel', releaseStick);
window.addEventListener('blur', releaseStick);

$('t-mic').addEventListener('click', () => toggleVoice());
$('t-recenter').addEventListener('click', () => controlling && recenter());
$('t-antennas').addEventListener('click', () => {
  const on = document.body.classList.toggle('show-antennas');
  $('t-antennas').classList.toggle('on', on);
  $('t-antennas').setAttribute('aria-pressed', String(on));
});

// ---- Control loop --------------------------------------------------------------
let lastTick = performance.now();
function tick() {
  const now = performance.now();
  const dt = Math.min((now - lastTick) / 1000, 0.1);
  lastTick = now;
  if (!controlling) return;

  const spin = clamp((held.has('KeyQ') ? 1 : 0) - (held.has('KeyE') ? 1 : 0) - stick.x, -1, 1);
  const lift = clamp((held.has('Space') ? 1 : 0) - (held.has('KeyC') ? 1 : 0) - stick.y, -1, 1);
  if (spin) turnBody(spin * BODY_RATE * dt);
  if (lift) pose.z += lift * Z_RATE * dt;
  clampPose();

  const moved = slew(dt);
  if (!(moved || dirty)) return;
  if (motion.send(target())) dirty = false;
  renderTelemetry();
}

// Worker timers keep ticking when the tab is in the background.
try {
  const src = `setInterval(() => postMessage(0), ${TICK_MS});`;
  const worker = new Worker(URL.createObjectURL(new Blob([src], { type: 'text/javascript' })));
  worker.onmessage = tick;
} catch {
  setInterval(tick, TICK_MS);
}

// ---- Video + voice -------------------------------------------------------------
const stream = new RobotStream({ url: wsURL('/signalling'), videoEl: video });
stream.addEventListener('status', (e) => {
  const { state, detail } = e.detail;
  const map = { live: ['ok', 'live'], connecting: ['warn', detail], waiting: ['warn', 'no stream'], warn: ['warn', detail], error: ['bad', detail] };
  const [cls, text] = map[state] || ['', state];
  setStatus('video', cls, text);
  if (state === 'waiting') {
    $('novideo-detail').textContent =
      'The daemon is not publishing a camera stream. On macOS, run the daemon in a plain terminal window (not tmux/screen) whose app has Camera permission, then restart it.';
  } else if (state === 'error') {
    $('novideo-detail').textContent = `Retrying: ${detail}`;
  }
});
video.addEventListener('playing', () => document.body.classList.add('has-video'));
video.addEventListener('emptied', () => document.body.classList.remove('has-video'));

let micTrack = null;
async function setupMic() {
  if (!window.isSecureContext || !navigator.mediaDevices) {
    // getUserMedia only exists on localhost or HTTPS.
    setStatus('mic', 'bad', 'needs HTTPS');
    return;
  }
  try {
    const media = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    micTrack = media.getAudioTracks()[0];
    micTrack.enabled = false;
    await stream.setMicTrack(micTrack);
    renderVoice();
  } catch (e) {
    console.warn('microphone unavailable', e);
    setStatus('mic', 'bad', 'mic blocked');
  }
}

function toggleVoice() {
  if (!micTrack) return;
  micTrack.enabled = !micTrack.enabled;
  renderVoice();
}

function renderVoice() {
  const on = !!micTrack?.enabled;
  setStatus('mic', on ? 'live' : '', on ? 'LIVE' : 'off (M)');
  $('voice-badge').hidden = !on;
  $('t-mic').classList.toggle('on', on);
  $('t-mic').setAttribute('aria-pressed', String(on));
}

// ---- Buttons -------------------------------------------------------------------
async function playMove(path) {
  try {
    await fetch(`/api/move/play/${path}`, { method: 'POST' });
    // Wait for the move to finish, then pick up the pose it left us in.
    for (let i = 0; i < 50; i++) {
      await new Promise((r) => setTimeout(r, 200));
      const running = await (await fetch('/api/move/running')).json();
      if (!running.length) break;
    }
    await reseed();
  } catch (e) {
    console.warn(`${path} failed`, e);
  }
}
// Gentler than the daemon's wake_up emote (which ends in fast 0.2s wiggles):
// torque on holding the current pose, then one slow glide to neutral.
async function gentleWake() {
  try {
    await fetch('/api/motors/set_mode/enabled', { method: 'POST' });
    await new Promise((r) => setTimeout(r, 1000));
    await fetch('/api/move/goto', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        head_pose: { x: 0, y: 0, z: 0, roll: 0, pitch: 0, yaw: 0 },
        antennas: ANTENNA_NEUTRAL,
        body_yaw: 0,
        duration: 4,
        interpolation: 'minjerk',
      }),
    });
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 200));
      const running = await (await fetch('/api/move/running')).json();
      if (!running.length) break;
    }
    await reseed();
  } catch (e) {
    console.warn('wake failed', e);
  }
}
$('btn-wake').addEventListener('click', gentleWake);
$('btn-sleep').addEventListener('click', () => playMove('goto_sleep'));

$('btn-start').addEventListener('click', async () => {
  $('overlay').hidden = true;
  controlling = true;
  await reseed();
  stream.start();
  video.play().catch(() => {});
  setupMic();
  lockPointer();
});

function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}
