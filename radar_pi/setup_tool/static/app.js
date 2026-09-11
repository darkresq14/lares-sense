'use strict';
/*
 * Lares Setup Tool — staged live view + config editor (issues #9–#13).
 *
 * Auto-connects to the frame WebSocket served on the same host:port as this
 * page and renders the stream with THREE.Points using preallocated typed
 * arrays: per frame we write into the existing Float32Attributes, flip
 * needsUpdate, and setDrawRange to the live point count (ADR-0004's
 * rendering pattern). On top of the cloud, the room preview overlays
 * (issue #13, geometry from overlays.js): boundary/presence/static
 * wireframe boxes, a sensor marker at its mount height with tilt
 * orientation, an FOV cone, and track ID labels that follow tracked
 * people. A color-by selector recolors points by height, SNR, doppler,
 * or owning track. Overlays rebuild live from the editor's DOM values —
 * every keystroke previews against the cloud, no Apply needed.
 *
 * Since issue #14 the page is safe for a crowd: **one editing session at a
 * time** — this tab requests the editing lock when its editor opens
 * (edit_begin / edit_end over the WS stream); if another browser owns it,
 * this one renders read-only with a "config locked" banner, and the lock
 * frees when that tab goes away (the page re-requests on the release
 * event). In Running Stage all edit controls are hidden entirely — the
 * page is a live monitor (cloud, overlays, tracks) with no way to touch
 * anything.
 */

// --- constants ---------------------------------------------------------

var MAX_POINTS = 2048;          // hard cap on the preallocated buffers
var HEIGHT_RANGE = 2.5;         // meters mapped onto the color ramp
var RECONNECT_DELAY_MS = 1000;

// --- module state ------------------------------------------------------

var scene, camera, renderer, geometry, pointsMaterial;
var statusEl, statFrameEl, statMetaEl;
var stageLine, healthLine, startButton, stopButton;
var wsInstance = null;

// Stage machine state (issue #12): 'config' | 'running', plus sensor
// health 'stopped' | 'reconfiguring' | 'streaming' | 'error'. The host
// snapshots both on connect and broadcasts every change.
var currentStage = 'config';
var currentHealth = 'stopped';
var stageReason = null;

// Editing lock (issue #14): editOwned — this tab holds the single editing
// session; editHeld — some tab holds it. The host answers edit_begin with
// a verdict and broadcasts every take/release, so every browser flips to
// read-only (or back) at the same moment, without a reload.
var editOwned = false;
var editHeld = false;

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

  // Room preview overlays (issue #13): boxes + sensor marker + FOV cone
  // live in one group, track ID labels in another (they update per frame,
  // not per config edit). Contents are (re)built by rebuildOverlays() /
  // updateTrackLabels().
  overlayRoot = new THREE.Group();
  scene.add(overlayRoot);
  labelGroup = new THREE.Group();
  scene.add(labelGroup);

  window.addEventListener('resize', onResize);
  initOrbit(canvas);
  applyCamera();
}

function onResize() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}

// Minimal pointer orbit (drag = rotate, wheel = zoom); enough for the
// setup view without vendoring OrbitControls.
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
    // A fresh connection owns nothing: the server freed our old lock when
    // the previous socket died. The stage snapshot (first message) drives
    // the first edit_begin via maybeRequestEditLock().
    editOwned = false;
    editHeld = false;
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
    else if (message.type === 'stage') onStage(message);
    else if (message.type === 'lock') onLock(message);
  };
  ws.onclose = function () {
    setStatus('err', 'disconnected — retrying');
    editOwned = false;
    editHeld = false;
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
  for (var i = 0; i < n; i++) {
    var p = pts[i]; // [x, y, z, doppler]
    pos[i * 3] = p[0];
    pos[i * 3 + 1] = p[1];
    pos[i * 3 + 2] = p[2];
  }
  geometry.attributes.position.needsUpdate = true;
  geometry.setDrawRange(0, n);
  lastFrameData = frame;
  applyPointColors(frame);
  updateTrackLabels(frame);

  statFrameEl.textContent =
    'frame ' + frame.frame_num + ' · ' + pts.length + ' pts';
  statMetaEl.textContent =
    (frame.tracks ? frame.tracks.length : 0) + ' tracks · ' + fpsValue +
    ' fps';
}

function setStatus(cls, text) {
  statusEl.className = cls;
  statusEl.textContent = text;
}

// --- room preview overlays (issue #13) ---------------------------------

var overlayRoot = null;        // THREE.Group: boxes, sensor marker, FOV cone
var labelGroup = null;         // THREE.Group: track ID label sprites
var overlayParams = null;      // last good readOverlayParams() result

// The main room box is prominent (page accent); presence and static
// boxes are deliberately dimmer — secondary geometry at a glance.
var ACCENT = 0xffb454;            // matches --accent in index.html
var ACCENT_CSS = '#ffb454';
var DIM_TEAL = 0x3d9c90;
var BOX_STYLES = {
  boundary: { color: ACCENT, opacity: 0.9 },
  presence: { color: DIM_TEAL, opacity: 0.45 },
  staticBox: { color: 0x5c7ea8, opacity: 0.45 },
};

function wireframe(edges, color, opacity) {
  var geo = new THREE.BufferGeometry();
  geo.setAttribute('position',
                   new THREE.BufferAttribute(new Float32Array(edges), 3));
  var mat = new THREE.LineBasicMaterial({
    color: color, transparent: opacity < 1, opacity: opacity });
  return new THREE.LineSegments(geo, mat);
}

function sensorMarker(sensor) {
  // A small sphere at the mount point plus a translucent cone along the
  // boresight: together they read as "sensor, this high, tilted so".
  var group = new THREE.Group();
  var pos = new THREE.Vector3(0, 0, sensor.z);
  var d = LaresOverlays.boresight(sensor.azimuth, sensor.elevation);
  var dir = new THREE.Vector3(d[0], d[1], d[2]);

  var sphere = new THREE.Mesh(
    new THREE.SphereGeometry(0.06, 12, 8),
    new THREE.MeshBasicMaterial({ color: ACCENT }));
  sphere.position.copy(pos);
  group.add(sphere);

  var cone = new THREE.Mesh(
    new THREE.ConeGeometry(0.07, 0.24, 12),
    new THREE.MeshBasicMaterial({ color: ACCENT, transparent: true,
                                  opacity: 0.55 }));
  cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
  cone.position.copy(pos).addScaledVector(dir, 0.2);
  group.add(cone);
  return group;
}

function fovCone(params) {
  // Range policy lives with the rest of the geometry math: overlays.js.
  return wireframe(LaresOverlays.fovEdges(params), DIM_TEAL, 0.3);
}

function rebuildOverlays() {
  if (!overlayRoot) return;
  clearGroup(overlayRoot);
  if (!overlayParams) return;
  for (var key in BOX_STYLES) {
    var style = BOX_STYLES[key];
    overlayRoot.add(wireframe(LaresOverlays.boxEdges(overlayParams[key]),
                              style.color, style.opacity));
  }
  overlayRoot.add(sensorMarker(overlayParams.sensor));
  overlayRoot.add(fovCone(overlayParams));
}

function updateOverlayParamsFrom(values) {
  // Live preview: parse the values as they currently sit in the editor
  // (strings from the DOM are fine). A bad or half-typed set keeps the
  // last good overlays on screen instead of flashing junk.
  var parsed = LaresOverlays.readOverlayParams(values);
  if (parsed) {
    overlayParams = parsed;
    rebuildOverlays();
  }
}

function clearGroup(group) {
  for (var i = group.children.length - 1; i >= 0; i--) {
    var child = group.children[i];
    group.remove(child);
    disposeObject(child);
  }
}

function disposeObject(obj) {
  obj.traverse(function (node) {
    if (node.geometry) node.geometry.dispose();
    if (node.material) {
      if (node.material.map) node.material.map.dispose();
      node.material.dispose();
    }
  });
}

// --- track ID labels -----------------------------------------------------

var labelPool = {};         // tid -> THREE.Sprite (visible iff tracked)
var labelPoolOrder = [];    // insertion order, oldest evicted first
var LABEL_POOL_MAX = 64;

function drawLabel(canvas, tid) {
  var ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = 'rgba(11, 14, 17, 0.78)';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = ACCENT_CSS;
  ctx.lineWidth = 3;
  ctx.strokeRect(1.5, 1.5, canvas.width - 3, canvas.height - 3);
  ctx.fillStyle = ACCENT_CSS;
  ctx.font = 'bold 40px ui-monospace, Menlo, Consolas, monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(String(tid), canvas.width / 2, canvas.height / 2 + 2);
}

function labelFor(tid) {
  var sprite = labelPool[tid];
  if (sprite) return sprite;
  if (labelPoolOrder.length >= LABEL_POOL_MAX) {
    var evict = labelPoolOrder.shift();
    var old = labelPool[evict];
    delete labelPool[evict];
    if (old) {
      labelGroup.remove(old);
      disposeObject(old);
    }
  }
  var canvas = document.createElement('canvas');
  canvas.width = 128;
  canvas.height = 64;
  drawLabel(canvas, tid);
  var tex = new THREE.CanvasTexture(canvas);
  tex.minFilter = THREE.LinearFilter;
  sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: tex, transparent: true }));
  sprite.scale.set(0.44, 0.22, 1);
  labelGroup.add(sprite);
  labelPool[tid] = sprite;
  labelPoolOrder.push(tid);
  return sprite;
}

function updateTrackLabels(frame) {
  var tracks = frame.tracks || [];
  var heights = {};
  var th = frame.track_heights || [];
  for (var h = 0; h < th.length; h++) heights[th[h].tid] = th[h];

  var seen = {};
  for (var i = 0; i < tracks.length; i++) {
    var t = tracks[i];
    var pos = t.position || [0, 0, 0];
    var sprite = labelFor(t.tid);
    // Hover the label over the person's head: above the tracked height
    // extent when the height TLV is present, else above the centroid.
    var top = heights[t.tid] ? heights[t.tid].max_z : pos[2] + 0.4;
    sprite.position.set(pos[0], pos[1], Math.max(top + 0.25, pos[2] + 0.5));
    sprite.visible = true;
    seen[t.tid] = true;
  }
  for (var tid in labelPool) {
    if (!seen[tid]) labelPool[tid].visible = false;
  }
}

// --- point coloring (color-by selector) ----------------------------------

var colorBy = 'height';         // 'height' | 'snr' | 'doppler' | 'track'
var lastFrameData = null;       // kept so a selector change recolors now
var snrMaxLive = 1;             // running max SNR, decayed per frame
var dopplerSpanLive = 0.5;      // running max |doppler|, decayed per frame

function updateColorStats(frame) {
  // SNR arrives in dB (OOB TLV 7) or linear (people-tracking TLV 1020);
  // a slowly-decaying running max normalizes either onto the ramp
  // without firmware branching, and without per-frame flicker.
  var maxSnr = 0;
  var snr = frame.snr || [];
  for (var i = 0; i < snr.length; i++) {
    if (snr[i] > maxSnr) maxSnr = snr[i];
  }
  var maxAbsV = 0;
  var pts = frame.points || [];
  for (var j = 0; j < pts.length; j++) {
    var v = Math.abs(pts[j][3]);
    if (v > maxAbsV) maxAbsV = v;
  }
  snrMaxLive = Math.max(maxSnr, snrMaxLive * 0.95, 1);
  dopplerSpanLive = Math.max(maxAbsV, dopplerSpanLive * 0.95, 0.25);
}

function pointColorInto(i, frame, out, offset) {
  var rgb;
  if (colorBy === 'snr') {
    var s = frame.snr && i < frame.snr.length ? frame.snr[i] : 0;
    rgb = LaresOverlays.rampRGB(LaresOverlays.snrT(s, snrMaxLive));
  } else if (colorBy === 'doppler') {
    rgb = LaresOverlays.rampRGB(
      LaresOverlays.dopplerT(frame.points[i][3], dopplerSpanLive));
  } else if (colorBy === 'track') {
    // TLV 1011: per-point owning track id; 253/254/255 and missing mean
    // unassociated — trackRGB() colors those dim gray.
    var idx = frame.track_indexes && i < frame.track_indexes.length
      ? frame.track_indexes[i] : null;
    if (idx === undefined) idx = null;
    rgb = LaresOverlays.trackRGB(idx);
  } else {
    rgb = LaresOverlays.rampRGB(
      LaresOverlays.heightT(frame.points[i][2], HEIGHT_RANGE));
  }
  out[offset] = rgb[0];
  out[offset + 1] = rgb[1];
  out[offset + 2] = rgb[2];
}

function applyPointColors(frame) {
  if (!frame || !frame.points) return;
  updateColorStats(frame);
  var n = Math.min(frame.points.length, MAX_POINTS);
  var col = geometry.attributes.color.array;
  for (var i = 0; i < n; i++) pointColorInto(i, frame, col, i * 3);
  geometry.attributes.color.needsUpdate = true;
}

// --- two-stage machine (issue #12) -------------------------------------

var HEALTH_TEXT = {
  stopped: 'sensor stopped',
  streaming: 'sensor streaming',
  reconfiguring: 'reconfiguring…',
  error: 'error'
};

function onStage(message) {
  currentStage = message.stage || currentStage;
  currentHealth = message.health || currentHealth;
  stageReason = message.reason || null;
  updateStageUi(!!message.locked);
  // Back in Config Stage (first snapshot, or Stop just landed): claim the
  // editing session if this tab's editor is open and nobody holds it.
  maybeRequestEditLock();
}

function updateStageUi(locked) {
  var running = currentStage === 'running';
  var busy = currentHealth === 'reconfiguring';

  stageLine.textContent = running ? 'running' : 'config stage';
  stageLine.className = running ? 'running' : (busy ? 'busy' : '');

  var healthText = HEALTH_TEXT[currentHealth] || currentHealth;
  if (currentHealth === 'error' && stageReason) {
    healthText += ' — ' + stageReason;
  }
  healthLine.textContent = healthText;
  healthLine.className = currentHealth === 'error' ? 'err' : '';

  startButton.disabled = running || busy;
  stopButton.disabled = !running || busy;

  // Running Stage: read-only monitor — the CSS hides the edit controls
  // entirely (issue #14); Config Stage: editable unless another browser
  // owns the editing session.
  document.body.classList.toggle('stage-running', running);
  updateEditorAccess();
  if (locked) {
    setConfigStatus('err', 'config locked — Stop to edit');
  }
}

// --- editing lock (issue #14) ------------------------------------------

var lockBanner;

function onLock(message) {
  if (typeof message.granted === 'boolean') {
    editOwned = message.granted;   // the verdict on our edit_begin
  }
  editHeld = !!message.held;
  if (!editHeld) editOwned = false;   // a release frees everyone's view
  updateEditorAccess();
  // The lock just freed and this tab wants to edit: try to take it (the
  // host arbitrates if several browsers race — one wins, the rest render
  // read-only again).
  maybeRequestEditLock();
}

function updateEditorAccess() {
  var running = currentStage === 'running';
  var lockedByOther = !running && editHeld && !editOwned;
  document.body.classList.toggle('edit-locked', lockedByOther);
  lockBanner.textContent = running
    ? 'Running — config locked. Stop to edit.'
    : 'Config locked — another browser is editing. Read-only view.';
  setEditorEditable(!running && !lockedByOther);
}

function editorIsOpen() {
  return document.body.classList.contains('editor-open');
}

function maybeRequestEditLock() {
  if (currentStage !== 'config') return;
  if (editOwned || editHeld) return;
  if (!editorIsOpen()) return;
  if (!wsInstance || wsInstance.readyState !== 1) return;
  wsInstance.send(JSON.stringify({ type: 'edit_begin' }));
}

function releaseEditLock() {
  if (!editOwned) return;
  editOwned = false;
  if (wsInstance && wsInstance.readyState === 1) {
    wsInstance.send(JSON.stringify({ type: 'edit_end' }));
  }
  updateEditorAccess();
}

function setEditorEditable(editable) {
  var inputs = editorBody.querySelectorAll('input');
  for (var i = 0; i < inputs.length; i++) inputs[i].disabled = !editable;
  setButtonsEnabled(editable && configSchema !== null && !applyPending);
}

function startStage() {
  if (!wsInstance || wsInstance.readyState !== 1) return;
  if (currentStage === 'running' || currentHealth === 'reconfiguring') return;
  wsInstance.send(JSON.stringify({ type: 'start' }));
}

function stopStage() {
  if (!wsInstance || wsInstance.readyState !== 1) return;
  if (currentStage !== 'running' || currentHealth === 'reconfiguring') return;
  wsInstance.send(JSON.stringify({ type: 'stop' }));
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
  updateOverlayParamsFrom(configValues);
  updateStageUi(false);   // re-enables buttons/inputs iff not Running
}

function onConfigSaved(message) {
  configValues = message.values;
  clearFieldErrors();
  setConfigStatus('ok', 'saved ✓');
  saveButton.disabled = false;
  updateOverlayParamsFrom(configValues);
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
  stageLine = document.getElementById('stage-line');
  healthLine = document.getElementById('health-line');
  startButton = document.getElementById('start');
  stopButton = document.getElementById('stop');
  editorBody = document.getElementById('editor-body');
  saveButton = document.getElementById('save');
  applyButton = document.getElementById('apply');
  configStatus = document.getElementById('config-status');
  applyStatus = document.getElementById('apply-status');
  lockBanner = document.getElementById('lock-banner');

  document.body.classList.add('editor-open');
  document.getElementById('editor-toggle').addEventListener('click', function () {
    var open = document.body.classList.toggle('editor-open');
    // Opening the editor claims the editing session; closing it releases
    // the lock for whoever else wants to edit (issue #14).
    if (open) maybeRequestEditLock();
    else releaseEditLock();
  });
  saveButton.addEventListener('click', saveConfig);
  applyButton.addEventListener('click', applyConfig);
  startButton.addEventListener('click', startStage);
  stopButton.addEventListener('click', stopStage);

  // Live overlay preview: every keystroke in the editor re-reads the
  // current DOM values and rebuilds the room geometry against the live
  // cloud — seeing the change costs nothing, Apply is only for the
  // sensor (issue #13's no-apply preview).
  editorBody.addEventListener('input', function () {
    updateOverlayParamsFrom(collectValues());
  });

  var colorBySelect = document.getElementById('color-by');
  colorBySelect.addEventListener('change', function () {
    colorBy = colorBySelect.value;
    applyPointColors(lastFrameData);   // recolor now, not next frame
  });

  initScene();
  connect();
  requestAnimationFrame(animate);
})();
