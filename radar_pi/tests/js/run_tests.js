// Plain-node tests for static/overlays.js (issue #13).
//
// The python suite does not run these; run directly:
//     node radar_pi/tests/js/run_tests.js
// Zero dependencies by design (the Pi repo carries no node toolchain) —
// this file is plain assert-style checks of the overlay math that has no
// browser seam.

'use strict';

const path = require('path');
const LaresOverlays = require(path.join(__dirname, '..', '..',
                                        'setup_tool', 'static',
                                        'overlays.js'));

let failed = 0;
let ran = 0;

function check(name, fn) {
  ran++;
  try {
    fn();
  } catch (err) {
    failed++;
    console.error(`FAIL ${name}: ${err && err.message}`);
  }
}

function ok(cond, msg) {
  if (!cond) throw new Error(msg || 'expected true');
}

function eq(actual, expected, msg) {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a !== b) throw new Error(`${msg || 'mismatch'}: ${a} != ${b}`);
}

function approx(actual, expected, tol, msg) {
  if (Math.abs(actual - expected) > tol) {
    throw new Error(`${msg || 'mismatch'}: ${actual} !~= ${expected}`);
  }
}

// ---- readOverlayParams ----------------------------------------------------

// Schema-shaped defaults (config_schema.py defaults() shape).
function defaults() {
  return {
    boundaryBox: [[-4, 4, 0, 8, 0, 3]],
    presenceBoundaryBox: [[-3, 3, 0.5, 7.5, 0, 3]],
    staticBoundaryBox: [[-3, 3, 0.5, 7.5, 0, 3]],
    sensorPosition: [[2, 0, 15]],
    fovCfg: [[-1, 70, 70]],
  };
}

check('reads schema defaults', () => {
  const p = LaresOverlays.readOverlayParams(defaults());
  ok(p, 'params parse');
  eq(p.boundary, { xMin: -4, xMax: 4, yMin: 0, yMax: 8, zMin: 0, zMax: 3 });
  eq(p.presence, { xMin: -3, xMax: 3, yMin: 0.5, yMax: 7.5, zMin: 0, zMax: 3 });
  eq(p.sensor, { z: 2, azimuth: 0, elevation: 15 });
  eq(p.fov, { azimuth: 70, elevation: 70 });
});

check('accepts numeric strings (live DOM values)', () => {
  const v = defaults();
  v.boundaryBox = [['-4.5', '4.5', '0', '9', '0', '3.2']];
  v.sensorPosition = [['2.5', '10', '-5']];
  const p = LaresOverlays.readOverlayParams(v);
  ok(p, 'string values parse');
  eq(p.boundary.xMin, -4.5);
  eq(p.boundary.yMax, 9);
  eq(p.sensor, { z: 2.5, azimuth: 10, elevation: -5 });
});

check('null on missing command', () => {
  const v = defaults();
  delete v.boundaryBox;
  eq(LaresOverlays.readOverlayParams(v), null);
});

check('null on non-numeric field', () => {
  const v = defaults();
  v.sensorPosition = [['', '0', '15']];   // cleared input is not zero
  eq(LaresOverlays.readOverlayParams(v), null);
  v.sensorPosition = [['high', '0', '15']];
  eq(LaresOverlays.readOverlayParams(v), null);
  v.sensorPosition = [[null, '0', '15']];  // Number(null)===0 trap
  eq(LaresOverlays.readOverlayParams(v), null);
});

check('null when a box has min > max', () => {
  const v = defaults();
  v.boundaryBox = [[4, -4, 0, 8, 0, 3]];  // x min > x max
  eq(LaresOverlays.readOverlayParams(v), null);
  v.boundaryBox = [[-4, 4, 0, 8, 3, 0]];  // z min > z max
  eq(LaresOverlays.readOverlayParams(v), null);
});

check('clamps out-of-range fov to a drawable pyramid', () => {
  const v = defaults();
  v.fovCfg = [[-1, 200, 0]];
  const p = LaresOverlays.readOverlayParams(v);
  eq(p.fov, { azimuth: 89, elevation: 1 });
});

check('null on null/undefined values', () => {
  eq(LaresOverlays.readOverlayParams(null), null);
  eq(LaresOverlays.readOverlayParams(undefined), null);
});

// ---- boxEdges ---------------------------------------------------------------

check('boxEdges emits 12 edges (24 endpoints)', () => {
  const e = LaresOverlays.boxEdges({ xMin: -4, xMax: 4, yMin: 0, yMax: 8,
                                     zMin: 0, zMax: 3 });
  eq(e.length, 12 * 6, 'edge count');
});

check('boxEdges endpoints are all corners, axis-aligned', () => {
  const box = { xMin: -1, xMax: 2, yMin: 3, yMax: 4, zMin: 0, zMax: 5 };
  const e = LaresOverlays.boxEdges(box);
  const seen = new Set();
  for (let i = 0; i < e.length; i += 6) {
    const a = [e[i], e[i + 1], e[i + 2]];
    const b = [e[i + 3], e[i + 4], e[i + 5]];
    // axis-aligned: exactly two coordinates shared per endpoint pair
    const shared = a.filter((c, k) => c === b[k]).length;
    eq(shared, 2, 'edge is axis-aligned');
    for (const c of [a, b]) {
      ok(c[0] === box.xMin || c[0] === box.xMax, 'x on a face');
      ok(c[1] === box.yMin || c[1] === box.yMax, 'y on a face');
      ok(c[2] === box.zMin || c[2] === box.zMax, 'z on a face');
      seen.add(c.join(','));
    }
  }
  eq(seen.size, 8, 'all 8 corners present');
});

// ---- boresight ---------------------------------------------------------------

check('untilted boresight is +y', () => {
  const d = LaresOverlays.boresight(0, 0);
  approx(d[0], 0, 1e-9);
  approx(d[1], 1, 1e-9);
  approx(d[2], 0, 1e-9);
});

check('positive azimuth swings boresight toward -X (+X->+Y rotation)', () => {
  const d = LaresOverlays.boresight(90, 0);
  approx(d[0], -1, 1e-9);
  approx(d[1], 0, 1e-9);
  approx(d[2], 0, 1e-9);
});

check('positive elevation tilts boresight down', () => {
  const d = LaresOverlays.boresight(0, 90);
  approx(d[0], 0, 1e-9);
  approx(d[1], 0, 1e-9);
  approx(d[2], -1, 1e-9);
});

check('boresight is unit length at combined tilts', () => {
  const d = LaresOverlays.boresight(37, -22);
  approx(Math.hypot(d[0], d[1], d[2]), 1, 1e-9);
});

// ---- fovEdges ----------------------------------------------------------------

check('fovEdges: 8 edges (4 apex + 4 rim), untilted rim spans +/- extents', () => {
  const p = LaresOverlays.readOverlayParams(defaults());
  p.sensor = { z: 2, azimuth: 0, elevation: 0 };
  const R = 6;
  const tan70 = Math.tan(70 * Math.PI / 180);
  const e = LaresOverlays.fovEdges(p, R);
  eq(e.length, 8 * 6, 'edge count');

  // Untilted: rim rectangle at y = R, x = +/- R*tan(70),
  // z = 2 +/- R*tan(70). Collect endpoints that are not the apex.
  const pts = [];
  for (let i = 0; i < e.length; i += 6) {
    for (const k of [0, 3]) {
      const q = [e[i + k], e[i + k + 1], e[i + k + 2]];
      if (q[0] !== 0 || q[1] !== 0 || q[2] !== 2) pts.push(q);
    }
  }
  const xs = pts.map(q => q[0]).sort((a, b) => a - b);
  const zs = pts.map(q => q[2]).sort((a, b) => a - b);
  approx(xs[0], -R * tan70, 1e-9);
  approx(xs[xs.length - 1], R * tan70, 1e-9);
  approx(zs[0], 2 - R * tan70, 1e-9);
  approx(zs[zs.length - 1], 2 + R * tan70, 1e-9);
  for (const q of pts) approx(q[1], R, 1e-9);
});

check('fovEdges: tilted rim is the rotated rectangle (planar, az extent kept)', () => {
  const p = LaresOverlays.readOverlayParams(defaults());
  p.sensor = { z: 2, azimuth: 0, elevation: 15 };
  const R = 6;
  const tan70 = Math.tan(70 * Math.PI / 180);
  const e = LaresOverlays.fovEdges(p, R);
  // Rim endpoints: local (x=+/-R*tan70, y=R, z=+/-R*tan70) pitched down
  // by 15 deg: y' = R*cos15 + z*sin15, z' = 2 - R*sin15 + z*cos15.
  const rim = [];
  for (let i = 0; i < e.length; i += 6) {
    for (const k of [0, 3]) {
      const q = [e[i + k], e[i + k + 1], e[i + k + 2]];
      if (q[0] !== 0 || q[1] !== 0 || q[2] !== 2) rim.push(q);
    }
  }
  // Corners repeat across apex/rim edges; verify every rim endpoint
  // against the expected rotation: local (x = +/-R*tan70, y = R,
  // z = +/-R*tan70) pitched down 15 deg.
  const el = 15 * Math.PI / 180;
  for (const q of rim) {
    ok(Math.abs(Math.abs(q[0]) - R * tan70) < 1e-6, 'local x extent kept');
    const localZ = (q[1] - R * Math.cos(el)) / Math.sin(el);
    ok(Math.abs(Math.abs(localZ) - R * tan70) < 1e-6, 'local z extent kept');
    approx(q[2], 2 - R * Math.sin(el) + localZ * Math.cos(el), 1e-9);
  }
});

check('fovEdges originates at the sensor mount point', () => {
  const p = LaresOverlays.readOverlayParams(defaults());
  p.sensor = { z: 3.25, azimuth: 10, elevation: 5 };
  const e = LaresOverlays.fovEdges(p, 4);
  // Every apex edge starts at (0, 0, z).
  for (let i = 0; i < 4 * 6; i += 6) {
    eq([e[i], e[i + 1], e[i + 2]], [0, 0, 3.25]);
  }
});

check('fovEdges defaults range to the bounded room depth', () => {
  const p = LaresOverlays.readOverlayParams(defaults());
  eq(LaresOverlays.fovRange(p), 8, 'boundary yMax');
  p.boundary.yMax = 100;
  eq(LaresOverlays.fovRange(p), 12, 'capped');
  p.boundary.yMax = 1;
  eq(LaresOverlays.fovRange(p), 4, 'floored');
  // No explicit range: untilted rim sits at the default distance along +y.
  const p0 = LaresOverlays.readOverlayParams(defaults());
  p0.sensor = { z: 2, azimuth: 0, elevation: 0 };
  const e = LaresOverlays.fovEdges(p0);
  const ys = e.filter((_, i) => i % 3 === 1);
  ok(ys.some(v => Math.abs(v - 8) < 1e-9), 'rim at default range');
});

// ---- coloring ----------------------------------------------------------------

check('rampRGB endpoints and clamping', () => {
  eq(LaresOverlays.rampRGB(0).map(c => Math.round(c * 255)), [64, 224, 208]);
  eq(LaresOverlays.rampRGB(1).map(c => Math.round(c * 255)), [255, 180, 84]);
  eq(LaresOverlays.rampRGB(-1), LaresOverlays.rampRGB(0));
  eq(LaresOverlays.rampRGB(2), LaresOverlays.rampRGB(1));
});

check('heightT maps onto 0..1 with clamp', () => {
  approx(LaresOverlays.heightT(1.25, 2.5), 0.5, 1e-9);
  eq(LaresOverlays.heightT(-3, 2.5), 0);
  eq(LaresOverlays.heightT(9, 2.5), 1);
});

check('snrT normalizes against running max', () => {
  approx(LaresOverlays.snrT(15, 30), 0.5, 1e-9);
  eq(LaresOverlays.snrT(60, 30), 1);
  eq(LaresOverlays.snrT(0, 0), 0);  // degenerate max guarded
});

check('dopplerT centers zero velocity', () => {
  approx(LaresOverlays.dopplerT(0, 1), 0.5, 1e-9);
  eq(LaresOverlays.dopplerT(-1, 1), 0);
  eq(LaresOverlays.dopplerT(1, 1), 1);
});

check('trackRGB: stable per id, distinct hues, gray for unassociated', () => {
  const a = LaresOverlays.trackRGB(3);
  eq(LaresOverlays.trackRGB(3), a, 'stable');
  const b = LaresOverlays.trackRGB(4);
  ok(JSON.stringify(a) !== JSON.stringify(b), 'distinct');
  const gray = [0.36, 0.40, 0.45];
  eq(LaresOverlays.trackRGB(253), gray, '253 gray');
  eq(LaresOverlays.trackRGB(255), gray, '255 gray');
  eq(LaresOverlays.trackRGB(null), gray, 'null gray');
  eq(LaresOverlays.trackRGB(undefined), gray, 'undefined gray');
  for (const tid of [0, 1, 2, 17, 240]) {
    for (const c of LaresOverlays.trackRGB(tid)) {
      ok(c >= 0 && c <= 1, 'in range');
    }
  }
});

// ---- summary -----------------------------------------------------------------

console.log(`${ran - failed}/${ran} passed`);
if (failed) process.exit(1);
