// WebGL antenna controller: a front view of Reachy's head. Drag an antenna
// tip to set its angle. Bright antennas are the commanded pose; faint ghosts
// show where the motors actually are.
import * as THREE from './vendor/three.module.min.js';

const LIMIT = 2.8; // rad, stays clear of the ±π joint limit
const LEN = 1.25;
// Robot-to-screen rotation sign per antenna, indexed [right, left] like the
// daemon's antenna arrays. The robot's right antenna is on the viewer's left.
// Rest pose on the robot reads roughly [-0.18, +0.17], i.e. mirrored poses
// have opposite signs. Flip these if dragging moves the real antenna the
// wrong way.
const SIGN = [1, 1];
const PIVOTS = [new THREE.Vector2(-0.62, 0.55), new THREE.Vector2(0.62, 0.55)];

export class AntennaPad {
  constructor(canvas, { onChange }) {
    this.canvas = canvas;
    this.onChange = onChange;
    this.mirror = true;
    this.commanded = [0, 0];
    this.drag = null;

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
    this.renderer = renderer;

    const aspect = canvas.clientWidth / canvas.clientHeight;
    const h = 2.2;
    this.camera = new THREE.OrthographicCamera(-h * aspect, h * aspect, h + 0.5, -h + 0.5, 0.1, 20);
    this.camera.position.set(0, 0, 10);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0b0f14);
    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(2, 3, 5);
    scene.add(key);
    this.scene = scene;

    this.buildRobot();
    this.live = PIVOTS.map((p) => this.buildAntenna(p, 0x5ee0a0, 1));
    this.ghost = PIVOTS.map((p) => this.buildAntenna(p, 0xffffff, 0.22));

    canvas.addEventListener('pointerdown', (e) => this.onPointerDown(e));
    canvas.addEventListener('pointermove', (e) => this.onPointerMove(e));
    canvas.addEventListener('pointerup', (e) => this.onPointerUp(e));
    canvas.addEventListener('pointercancel', (e) => this.onPointerUp(e));
    this.render();
  }

  buildRobot() {
    const shell = new THREE.MeshStandardMaterial({ color: 0xf2f2ee, roughness: 0.45 });
    const dark = new THREE.MeshStandardMaterial({ color: 0x15181c, roughness: 0.3 });

    const head = new THREE.Mesh(new THREE.SphereGeometry(1, 40, 24), shell);
    head.scale.set(1.05, 0.72, 0.8);
    head.position.y = 0.1;
    this.scene.add(head);

    for (const x of [-0.38, 0.38]) {
      const eye = new THREE.Mesh(new THREE.CircleGeometry(0.2, 32), dark);
      eye.position.set(x, 0.12, 0.81);
      this.scene.add(eye);
    }

    const body = new THREE.Mesh(new THREE.CylinderGeometry(0.75, 0.95, 1.1, 40), shell);
    body.position.y = -1.15;
    this.scene.add(body);
  }

  buildAntenna(pivot, color, opacity) {
    const mat = new THREE.MeshStandardMaterial({ color, roughness: 0.4, transparent: opacity < 1, opacity, depthWrite: opacity >= 1 });
    const group = new THREE.Group();
    group.position.set(pivot.x, pivot.y, opacity < 1 ? 0.3 : 0.4);

    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.05, LEN, 12), mat);
    stem.position.y = LEN / 2;
    group.add(stem);
    const tip = new THREE.Mesh(new THREE.SphereGeometry(0.13, 20, 12), mat);
    tip.position.y = LEN;
    group.add(tip);

    this.scene.add(group);
    return group;
  }

  setMirror(on) {
    this.mirror = on;
  }

  setCommanded(angles) {
    this.commanded = [...angles];
    this.live.forEach((g, i) => (g.rotation.z = SIGN[i] * angles[i]));
    this.render();
  }

  setActual(angles) {
    this.ghost.forEach((g, i) => (g.rotation.z = SIGN[i] * angles[i]));
    this.render();
  }

  render() {
    this.renderer.render(this.scene, this.camera);
  }

  toWorld(e) {
    const r = this.canvas.getBoundingClientRect();
    const v = new THREE.Vector3(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1, 0);
    v.unproject(this.camera);
    return new THREE.Vector2(v.x, v.y);
  }

  onPointerDown(e) {
    if (document.pointerLockElement) return;
    const p = this.toWorld(e);
    // Grab whichever antenna tip is closest.
    const dists = this.live.map((g, i) => {
      const a = g.rotation.z;
      const tip = new THREE.Vector2(PIVOTS[i].x - LEN * Math.sin(a), PIVOTS[i].y + LEN * Math.cos(a));
      return tip.distanceTo(p);
    });
    this.drag = dists[0] <= dists[1] ? 0 : 1;
    this.canvas.setPointerCapture(e.pointerId);
    this.canvas.classList.add('dragging');
    this.onPointerMove(e);
  }

  onPointerMove(e) {
    if (this.drag === null) return;
    const i = this.drag;
    const d = this.toWorld(e).sub(PIVOTS[i]);
    if (d.lengthSq() < 0.01) return;
    let angle = SIGN[i] * Math.atan2(-d.x, d.y);
    // atan2 wraps at ±π (straight down). Take the equivalent angle nearest the
    // current command, so dragging past the bottom clamps at the limit instead
    // of flipping the antenna to the other side and back.
    angle += 2 * Math.PI * Math.round((this.commanded[i] - angle) / (2 * Math.PI));
    angle = clamp(angle, -LIMIT, LIMIT);
    const next = [...this.commanded];
    next[i] = angle;
    if (this.mirror) next[1 - i] = -angle;
    this.setCommanded(next);
    this.onChange(next);
  }

  onPointerUp(e) {
    if (this.drag === null) return;
    this.drag = null;
    this.canvas.classList.remove('dragging');
    if (this.canvas.hasPointerCapture(e.pointerId)) this.canvas.releasePointerCapture(e.pointerId);
  }
}

function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}
