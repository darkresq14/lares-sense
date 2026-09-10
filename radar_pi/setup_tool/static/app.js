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
var wsInstance = null;

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
  wsInstance = ws;
  ws.onopen = function () {
    setStatus('ok', 'stream live');
    ws.send(JSON.stringify({ type: 'config_get' }));
  };
  ws.onmessage = function (event) {
    var message;
    try {
      message = JSON.parse(event.data);
    } catch (err) {
      return;
    }
    if (!message || !message.type) return;
    if (message.type === 'frame') onFrame(message.frame);
    else if (message.type === 'config') onConfig(message);
    else if (message.type === 'config_saved') onConfigSaved(message);
    else if (message.type === 'config_rejected') onConfigRejected(message);
    else if (message.type === 'apply_progress') onApplyProgress(message);
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

// --- tiered config editor (issue #10) ----------------------------------

var editorBody, saveButton, configStatus;
var configSchema = null;   // {tiers: [...], params: [...]} from the host
var configValues = null;   // current values backing the inputs

function onConfig(message) {
  configSchema = message.schema;
  configValues = message.values;
  renderEditor();
  setConfigStatus('', 'loaded');
  saveButton.disabled = false;
  applyButton.disabled = false;
}

function onConfigSaved(message) {
  configValues = message.values;
  clearFieldErrors();
  setConfigStatus('ok', 'saved ✓');
  saveButton.disabled = false;
}

function onConfigRejected(message) {
  clearFieldErrors();
  var byInput = {};
  var errors = message.errors || [];
  for (var i = 0; i < errors.length; i++) {
    var e = errors[i];
    var input = document.getElementById(fieldId(e.command, e.instance, e.field));
    if (!input) continue;
    input.classList.add('invalid');
    byInput[input.id] = (byInput[input.id] || []).concat(e.message);
  }
  for (var id in byInput) {
    var cell = document.getElementById(id).parentNode;
    var errEl = document.createElement('div');
    errEl.className = 'field-err';
    errEl.textContent = byInput[id].join(' · ');
    cell.parentNode.insertBefore(errEl, cell.nextSibling);
  }
  setConfigStatus('err', errors.length + ' invalid value' +
                  (errors.length === 1 ? '' : 's') + ' — not saved');
  saveButton.disabled = false;
  // If this rejection answered an Apply attempt, say so on the apply line
  // (a plain Save rejection leaves the sensor-untouched text alone).
  if (applyPending) {
    applyPending = false;
    setApplyStatus('err', 'apply blocked — fix invalid values first');
  }
}

function clearFieldErrors() {
  var nodes = editorBody.querySelectorAll('.field-err');
  for (var i = 0; i < nodes.length; i++) nodes[i].parentNode.removeChild(nodes[i]);
  var inputs = editorBody.querySelectorAll('input.invalid');
  for (var j = 0; j < inputs.length; j++) inputs[j].classList.remove('invalid');
}

function fieldId(command, instance, key) {
  return 'f-' + command + '-' + instance + '-' + key;
}

function setConfigStatus(cls, text) {
  configStatus.className = cls;
  configStatus.textContent = text;
}

function renderEditor() {
  editorBody.innerHTML = '';
  var tiers = configSchema.tiers;
  for (var t = 0; t < tiers.length; t++) {
    var tier = tiers[t];
    var section = document.createElement('section');
    section.className = 'tier';
    var h = document.createElement('h2');
    h.textContent = tier.label;
    section.appendChild(h);

    var blurb = document.createElement('p');
    blurb.className = 'blurb';
    blurb.textContent = tier.blurb;
    section.appendChild(blurb);

    for (var i = 0; i < configSchema.params.length; i++) {
      var param = configSchema.params[i];
      if (param.tier !== tier.id) continue;
      section.appendChild(renderParam(param));
    }
    editorBody.appendChild(section);
  }
}

function renderParam(param) {
  var block = document.createElement('div');
  block.className = 'param';

  var name = document.createElement('div');
  name.className = 'name';
  name.textContent = param.title;
  block.appendChild(name);

  var cmd = document.createElement('div');
  cmd.className = 'cmd';
  cmd.textContent = param.command + (param.instances > 1
                                     ? ' ×' + param.instances : '');
  block.appendChild(cmd);

  var info = document.createElement('div');
  info.className = 'info';
  info.textContent = param.info;
  block.appendChild(info);

  // Wide (compact) layout when there are too many fields for labeled rows.
  var wide = param.fields.length > 8;

  for (var instance = 0; instance < param.instances; instance++) {
    if (param.instances > 1) {
      var label = document.createElement('div');
      label.className = 'instance-label';
      label.textContent = (param.instance_label || 'instance') + ' ' + instance;
      block.appendChild(label);
    }

    var fields = document.createElement('div');
    fields.className = 'fields' + (wide ? ' wide' : '');
    for (var f = 0; f < param.fields.length; f++) {
      fields.appendChild(renderField(param, param.fields[f], instance, f, wide));
    }
    block.appendChild(fields);
  }
  return block;
}

function renderField(param, field, instance, index, wide) {
  var value = configValues[param.command][instance][index];

  var input = document.createElement('input');
  input.type = 'text';
  input.value = String(value);
  input.id = fieldId(param.command, instance, field.key);
  input.dataset.command = param.command;
  input.dataset.instance = String(instance);
  input.dataset.key = field.key;
  var tip = field.label +
    (field.min !== null ? ' · range [' + field.min + ', ' + field.max + ']' : '') +
    (field.unit ? ' ' + field.unit : '') +
    (field.info ? '\n' + field.info : '');
  input.title = tip;

  if (wide) {
    // compact grid: input only, tooltip carries the label
    var cell = document.createElement('div');
    cell.className = 'field-cell';
    cell.appendChild(input);
    return cell;
  }

  var label = document.createElement('label');
  label.htmlFor = input.id;
  label.textContent = field.label;

  var cell = document.createElement('div');
  cell.className = 'field-cell';
  cell.appendChild(input);
  if (field.unit) {
    var unit = document.createElement('span');
    unit.className = 'unit';
    unit.textContent = field.unit;
    cell.appendChild(unit);
  }

  var frag = document.createDocumentFragment();
  frag.appendChild(label);
  frag.appendChild(cell);
  return frag;
}

function collectValues() {
  var values = {};
  for (var command in configValues) values[command] = [];
  var inputs = editorBody.querySelectorAll('input');
  for (var i = 0; i < inputs.length; i++) {
    var input = inputs[i];
    var c = input.dataset.command;
    var n = parseInt(input.dataset.instance, 10);
    while (values[c].length <= n) values[c].push([]);
    values[c][n].push(input.value);
  }
  return values;
}

function saveConfig() {
  if (!wsInstance || wsInstance.readyState !== 1) return;
  clearFieldErrors();
  setConfigStatus('', 'saving…');
  saveButton.disabled = true;   // re-enabled by the saved/rejected reply
  wsInstance.send(JSON.stringify({ type: 'config_set', values: collectValues() }));
}

// --- blind apply with frame-resume feedback (issue #11) -----------------

var applyButton, applyStatus;
var applyPending = false;      // an Apply is awaiting its verdict

function setButtonsEnabled(enabled) {
  saveButton.disabled = !enabled;
  applyButton.disabled = !enabled;
}

function applyConfig() {
  if (!wsInstance || wsInstance.readyState !== 1) return;
  clearFieldErrors();
  setConfigStatus('', 'saving…');
  setButtonsEnabled(false);    // re-enabled by progress's terminal states
  applyPending = true;
  setApplyStatus('busy', 'applying…');
  // One round trip: the host validates (rejecting before any serial byte),
  // persists the Working Config, and reboots the sensor with it.
  wsInstance.send(JSON.stringify({ type: 'apply', values: collectValues() }));
}

// Progress states mirror the host's blind sequence: reconfiguring →
// frames lost → frames resumed ✓. The config channel has no ACKs
// (ADR-0001) — frames resuming is the only success signal.
function onApplyProgress(message) {
  switch (message.state) {
  case 'reconfiguring':
    setApplyStatus('busy', 'reconfiguring…');
    break;
  case 'frames_lost':
    setApplyStatus('busy', 'frames lost…');
    break;
  case 'frames_resumed':
    setApplyStatus('ok', 'frames resumed ✓');
    applyPending = false;
    setButtonsEnabled(true);
    break;
  case 'failed':
    setApplyStatus('err', 'apply failed — ' + (message.reason || '?'));
    applyPending = false;
    setButtonsEnabled(true);
    break;
  }
}

function setApplyStatus(cls, text) {
  applyStatus.className = cls;
  applyStatus.textContent = text;
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
  editorBody = document.getElementById('editor-body');
  saveButton = document.getElementById('save');
  applyButton = document.getElementById('apply');
  configStatus = document.getElementById('config-status');
  applyStatus = document.getElementById('apply-status');

  document.body.classList.add('editor-open');
  document.getElementById('editor-toggle').addEventListener('click', function () {
    document.body.classList.toggle('editor-open');
  });
  saveButton.addEventListener('click', saveConfig);
  applyButton.addEventListener('click', applyConfig);

  initScene();
  connect();
  requestAnimationFrame(animate);
})();
