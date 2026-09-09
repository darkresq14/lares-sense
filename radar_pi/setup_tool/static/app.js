'use strict';
/*
 * Lares Setup Tool — skeleton live point cloud (issue #9).
 *
 * Auto-connects to the frame WebSocket served on the same host:port as this
 * page and renders the stream with THREE.Points using preallocated typed
 * arrays: per frame we write into the existing Float32Attributes, flip
 * needsUpdate, and setDrawRange to the live point count (ADR-0004's
 * rendering pattern). Points are colored by height for a cheap sense of
 * the room's vertical extent; overlays come in later tickets.
 */

// --- constants ---------------------------------------------------------

var MAX_POINTS = 2048;          // hard cap on the preallocated buffers
var HEIGHT_RANGE = 2.5;         // meters mapped onto the color ramp
var RECONNECT_DELAY_MS = 1000;

// color ramp endpoints (deep teal -> amber), matching the page accent
var RAMP_LOW = [64, 224, 208];
var RAMP_HIGH = [255, 180, 84];

// --- module state ------------------------------------------------------

var scene, camera, renderer, geometry, pointsMaterial;
var statusEl, statFrameEl, statMetaEl;

var framesSeen = 0;
var fpsCounter = 0;
var fpsValue = 0;
var lastFpsAt = 0;

// manual orbit: spherical camera around a room-ish target (z is up in the
// parser's frame; the sensor sits at the origin looking along +y)
var camTarget = new THREE.Vector3(0, 2.5, 1.0);
var camTheta = -Math.PI / 2;    // azimuth
var camPhi = 0.9;               // elevation
var camDist = 7.0;

// --- 3D scene ----------------------------------------------------------

function initScene() {
  var canvas = document.getElementById('view');
  renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setSize(window.innerWidth, window.innerHeight);

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b0e11);

  camera = new THREE.PerspectiveCamera(
    55, window.innerWidth / window.innerHeight, 0.1, 100);
  camera.up.set(0, 0, 1);

  // Preallocated point cloud: positions + per-point colors, updated in
  // place every frame (DynamicDrawUsage), draw range set per frame.
  geometry = new THREE.BufferGeometry();
  var position = new THREE.BufferAttribute(
    new Float32Array(MAX_POINTS * 3), 3);
  var color = new THREE.BufferAttribute(new Float32Array(MAX_POINTS * 3), 3);
  position.setUsage(THREE.DynamicDrawUsage);
  color.setUsage(THREE.DynamicDrawUsage);
  geometry.setAttribute('position', position);
  geometry.setAttribute('color', color);
  geometry.setDrawRange(0, 0);

  pointsMaterial = new THREE.PointsMaterial({
    size: 0.05,
    vertexColors: true,
    sizeAttenuation: true,
    transparent: true,
    opacity: 0.95,
  });
  scene.add(new THREE.Points(geometry, pointsMaterial));

  // Floor reference: GridHelper defaults to the XZ plane; rotating it onto
  // XY puts it on the floor of the parser's z-up frame.
  var grid = new THREE.GridHelper(8, 8, 0x2a333c, 0x1a2129);
  grid.rotation.x = Math.PI / 2;
  scene.add(grid);

  // Sensor origin marker.
  var sensorDot = new THREE.Mesh(
    new THREE.SphereGeometry(0.06, 12, 8),
    new THREE.MeshBasicMaterial({ color: 0xffb454 }));
  scene.add(sensorDot);

  window.addEventListener('resize', onResize);
  initOrbit(canvas);
  applyCamera();
}

function onResize() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}

// Minimal pointer orbit (drag = rotate, wheel = zoom); enough for a skeleton
// without vendoring OrbitControls.
function initOrbit(canvas) {
  var dragging = false;
  var lastX = 0, lastY = 0;

  canvas.addEventListener('pointerdown', function (e) {
    dragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', function (e) {
    if (!dragging) return;
    camTheta -= (e.clientX - lastX) * 0.005;
    camPhi = clamp(camPhi + (e.clientY - lastY) * 0.005, 0.05, 1.45);
    lastX = e.clientX;
    lastY = e.clientY;
    applyCamera();
  });
  canvas.addEventListener('pointerup', function () { dragging = false; });
  canvas.addEventListener('wheel', function (e) {
    e.preventDefault();
    camDist = clamp(camDist * Math.exp(e.deltaY * 0.001), 1.5, 30);
    applyCamera();
  }, { passive: false });
}

function applyCamera() {
  camera.position.set(
    camTarget.x + camDist * Math.cos(camPhi) * Math.cos(camTheta),
    camTarget.y + camDist * Math.cos(camPhi) * Math.sin(camTheta),
    camTarget.z + camDist * Math.sin(camPhi));
  camera.lookAt(camTarget);
}

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

// --- frame stream ------------------------------------------------------

function wsUrl() {
  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return proto + '//' + location.host + '/';
}

function connect() {
  setStatus('connecting', 'connecting…');
  var ws;
  try {
    ws = new WebSocket(wsUrl());
  } catch (err) {
    scheduleReconnect();
    return;
  }
  ws.onopen = function () {
    setStatus('ok', 'stream live');
  };
  ws.onmessage = function (event) {
    var message;
    try {
      message = JSON.parse(event.data);
    } catch (err) {
      return;
    }
    if (message && message.type === 'frame') onFrame(message.frame);
  };
  ws.onclose = function () {
    setStatus('err', 'disconnected — retrying');
    scheduleReconnect();
  };
  ws.onerror = function () {
    ws.close();
  };
}

var reconnectTimer = null;
function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(function () {
    reconnectTimer = null;
    connect();
  }, RECONNECT_DELAY_MS);
}

function onFrame(frame) {
  framesSeen++;
  fpsCounter++;

  var pts = frame.points || [];
  var n = Math.min(pts.length, MAX_POINTS);

  var pos = geometry.attributes.position.array;
  var col = geometry.attributes.color.array;
  for (var i = 0; i < n; i++) {
    var p = pts[i]; // [x, y, z, doppler]
    pos[i * 3] = p[0];
    pos[i * 3 + 1] = p[1];
    pos[i * 3 + 2] = p[2];
    heightColor(p[2], col, i * 3);
  }
  geometry.attributes.position.needsUpdate = true;
  geometry.attributes.color.needsUpdate = true;
  geometry.setDrawRange(0, n);

  statFrameEl.textContent =
    'frame ' + frame.frame_num + ' · ' + pts.length + ' pts';
  statMetaEl.textContent =
    (frame.tracks ? frame.tracks.length : 0) + ' tracks · ' + fpsValue +
    ' fps';
}

function heightColor(z, out, offset) {
  var t = clamp(z / HEIGHT_RANGE, 0, 1);
  out[offset] = (RAMP_LOW[0] + (RAMP_HIGH[0] - RAMP_LOW[0]) * t) / 255;
  out[offset + 1] = (RAMP_LOW[1] + (RAMP_HIGH[1] - RAMP_LOW[1]) * t) / 255;
  out[offset + 2] = (RAMP_LOW[2] + (RAMP_HIGH[2] - RAMP_LOW[2]) * t) / 255;
}

function setStatus(cls, text) {
  statusEl.className = cls;
  statusEl.textContent = text;
}

// --- main loop ---------------------------------------------------------

function animate(now) {
  requestAnimationFrame(animate);

  if (!lastFpsAt) lastFpsAt = now;
  if (now - lastFpsAt >= 1000) {
    fpsValue = fpsCounter;
    fpsCounter = 0;
    lastFpsAt = now;
    if (framesSeen === 0) {
      statMetaEl.textContent = 'waiting for frames';
    }
  }

  renderer.render(scene, camera);
}

(function main() {
  statusEl = document.getElementById('status');
  statFrameEl = document.getElementById('stat-frame');
  statMetaEl = document.getElementById('stat-meta');

  initScene();
  connect();
  requestAnimationFrame(animate);
})();
