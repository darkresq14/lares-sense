'use strict';
/*
 * Lares Setup Tool — overlay geometry & coloring math (issue #13).
 *
 * Pure functions, no THREE and no DOM: everything here works on plain
 * arrays so the module is testable under node (tests/js/run_tests.js)
 * independent of the browser. app.js turns these numbers into
 * LineSegments / sprites / vertex colors.
 *
 * Coordinate frame (shared with the parser and ADR-0004's rendering
 * pattern): z is up, the sensor's floor projection sits at the origin,
 * and an untilted sensor looks along +y. The boundary boxes are already
 * expressed in this world frame (x/y/z min/max around the origin).
 */

(function (global) {
  // --- constants ---------------------------------------------------------

  // TLV 1011 values at or above this mean "point not associated with any
  // track" (weak SNR / out of bounds / noise) — tlv_parser.py's
  // TRACK_INDEX_* codes.
  var UNASSIGNED_MIN = 253;

  // Color ramp endpoints (deep teal -> amber), matching the page accent.
  var RAMP_LOW = [64, 224, 208];
  var RAMP_HIGH = [255, 180, 84];

  var DEG2RAD = Math.PI / 180;

  // --- helpers -----------------------------------------------------------

  function clamp01(t) {
    return t < 0 ? 0 : (t > 1 ? 1 : t);
  }

  function hslToRGB(h, s, l) {
    // Standard HSL -> RGB (all in 0..1); avoids a THREE dependency here.
    if (s === 0) {
      return [l, l, l];
    }
    function hue2rgb(p, q, t) {
      if (t < 0) t += 1;
      if (t > 1) t -= 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    }
    var q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    var p = 2 * l - q;
    return [hue2rgb(p, q, h + 1 / 3), hue2rgb(p, q, h), hue2rgb(p, q, h - 1 / 3)];
  }

  // --- config -> overlay parameters ----------------------------------------

  // Field indices inside each cfg command line, mirroring
  // config_schema.py's field order. One instance per command here
  // (all five have instances=1 in the schema).
  //   *_BoundaryBox: x_min x_max y_min y_max z_min z_max
  //   sensorPosition: z azim_tilt elev_tilt
  //   fovCfg: sub_frame_idx azimuth_fov elevation_fov
  function readOverlayParams(values) {
    // ``values``: the editor/config shape {command: [[fields...]]} with
    // numbers (host-normalized) or numeric strings (live DOM preview).
    // Returns null when any needed field is missing or non-numeric — the
    // caller keeps the last good overlays rather than rendering junk.
    if (!values) return null;
    function nums(command, count) {
      var instances = values[command];
      if (!instances || !instances[0]) return null;
      var fields = instances[0];
      if (fields.length < count) return null;
      var out = [];
      for (var i = 0; i < count; i++) {
        var raw = fields[i];
        // Number('') is 0 and Number(null) is 0 — a cleared or missing
        // field is invalid input, not zero.
        if (raw === null || raw === undefined ||
            (typeof raw === 'string' && raw.trim() === '')) return null;
        var n = Number(raw);
        if (!isFinite(n)) return null;
        out.push(n);
      }
      return out;
    }

    var boundary = nums('boundaryBox', 6);
    var presence = nums('presenceBoundaryBox', 6);
    var staticBox = nums('staticBoundaryBox', 6);
    var pos = nums('sensorPosition', 3);
    var fov = nums('fovCfg', 3);
    if (!boundary || !presence || !staticBox || !pos || !fov) return null;

    var boxes = [boundary, presence, staticBox];
    for (var b = 0; b < boxes.length; b++) {
      var f = boxes[b];
      if (f[0] > f[1] || f[2] > f[3] || f[4] > f[5]) return null; // min > max
    }

    // FOV extent is per side ([-fov, +fov]); clamp a mistyped value to a
    // drawable pyramid rather than dropping every overlay.
    function clampSide(v) {
      return Math.min(89, Math.max(1, v));
    }

    return {
      boundary: boxFrom(boundary),
      presence: boxFrom(presence),
      staticBox: boxFrom(staticBox),
      sensor: { z: pos[0], azimuth: pos[1], elevation: pos[2] },
      fov: { azimuth: clampSide(fov[1]), elevation: clampSide(fov[2]) },
    };
  }

  function boxFrom(f) {
    return {
      xMin: f[0], xMax: f[1],
      yMin: f[2], yMax: f[3],
      zMin: f[4], zMax: f[5],
    };
  }

  // --- geometry -----------------------------------------------------------

  function pushSeg(out, a, b) {
    out.push(a[0], a[1], a[2], b[0], b[1], b[2]);
  }

  function boxEdges(box) {
    // The 12 edges of an axis-aligned box as a flat [x1,y1,z1, x2,y2,z2,
    // ...] line-segment list (THREE.LineSegments layout). Edges: 4 along
    // each axis, joining the 8 corners.
    var xs = [box.xMin, box.xMax];
    var ys = [box.yMin, box.yMax];
    var zs = [box.zMin, box.zMax];
    var out = [];
    for (var j = 0; j <= 1; j++) {
      for (var k = 0; k <= 1; k++) {
        pushSeg(out, [xs[0], ys[j], zs[k]], [xs[1], ys[j], zs[k]]);  // x
        pushSeg(out, [xs[j], ys[0], zs[k]], [xs[j], ys[1], zs[k]]);  // y
        pushSeg(out, [xs[j], ys[k], zs[0]], [xs[j], ys[k], zs[1]]);  // z
      }
    }
    return out;
  }

  function boresight(azimuthDeg, elevationDeg) {
    // Unit direction the sensor looks at.
    // An untilted sensor looks along +y. Positive azimuth rotates +X
    // toward +Y (rotation about +z — config_schema's sensorPosition doc),
    // which swings the boresight toward -X. Positive elevation tilts the
    // boresight down toward the ground (wall mount: ~15 deg).
    var az = azimuthDeg * DEG2RAD;
    var el = elevationDeg * DEG2RAD;
    return [
      -Math.sin(az) * Math.cos(el),
      Math.cos(az) * Math.cos(el),
      -Math.sin(el),
    ];
  }

  function fovRange(params) {
    // How far the pyramid reaches: the room's depth, bounded — so the
    // cone and the boundary box tell one story without the cone drowning
    // the scene when the room is mistyped huge.
    var depth = params.boundary.yMax;
    return Math.min(12, Math.max(isFinite(depth) ? depth : 0, 4));
  }

  function fovEdges(params, range) {
    // The FOV "cone" as a 4-sided pyramid: the sensor position (the floor
    // origin raised to the mount height) to the four rim corners. The rim
    // is a rectangle in the sensor-local frame (boresight +y, right +x,
    // up +z) at distance ``range`` (default: fovRange) along the
    // boresight, spanning +/- tan(fov) per axis — the cfg value is the
    // extent on ONE side — then rotated by the sensor tilts into the
    // world frame. Same flat line-segment layout as boxEdges.
    if (!isFinite(range)) range = fovRange(params);
    var s = params.sensor;
    var origin = [0, 0, s.z];
    var tanA = Math.tan(params.fov.azimuth * DEG2RAD);
    var tanE = Math.tan(params.fov.elevation * DEG2RAD);
    var cosAz = Math.cos(s.azimuth * DEG2RAD);
    var sinAz = Math.sin(s.azimuth * DEG2RAD);
    var cosEl = Math.cos(s.elevation * DEG2RAD);
    var sinEl = Math.sin(s.elevation * DEG2RAD);
    var locals = [
      [-tanA, 1, -tanE], [-tanA, 1, tanE],
      [tanA, 1, -tanE], [tanA, 1, tanE],
    ];
    var rim = [];
    for (var c = 0; c < locals.length; c++) {
      var v = locals[c];
      // Rx(-elevation): positive elevation pitches +y down (-z).
      var y1 = v[1] * cosEl + v[2] * sinEl;
      var z1 = -v[1] * sinEl + v[2] * cosEl;
      // Rz(azimuth): positive rotates +X toward +Y.
      var x2 = v[0] * cosAz - y1 * sinAz;
      var y2 = v[0] * sinAz + y1 * cosAz;
      rim.push([
        origin[0] + x2 * range,
        origin[1] + y2 * range,
        origin[2] + z1 * range,
      ]);
    }
    // rim index: 0 = (-az, -el), 1 = (-az, +el), 2 = (+az, -el), 3 = (+az, +el)
    var out = [];
    for (var i = 0; i < 4; i++) pushSeg(out, origin, rim[i]);
    pushSeg(out, rim[0], rim[1]);
    pushSeg(out, rim[1], rim[3]);
    pushSeg(out, rim[3], rim[2]);
    pushSeg(out, rim[2], rim[0]);
    return out;
  }

  // --- point coloring (color-by selector) ----------------------------------

  function rampRGB(t) {
    // The teal -> amber ramp shared with the page accent, 0..1 -> [r,g,b].
    t = clamp01(t);
    return [
      (RAMP_LOW[0] + (RAMP_HIGH[0] - RAMP_LOW[0]) * t) / 255,
      (RAMP_LOW[1] + (RAMP_HIGH[1] - RAMP_LOW[1]) * t) / 255,
      (RAMP_LOW[2] + (RAMP_HIGH[2] - RAMP_LOW[2]) * t) / 255,
    ];
  }

  function heightT(z, heightRange) {
    return clamp01(z / (heightRange || 2.5));
  }

  function snrT(snr, snrMax) {
    // Normalized against a running max the page maintains (SNR is dB for
    // the OOB TLV 7, linear for the people-tracking TLV 1020 — a dynamic
    // max handles both without firmware branching).
    return clamp01(snr / Math.max(snrMax, 1e-6));
  }

  function dopplerT(doppler, span) {
    // Signed velocity mapped onto the full ramp: slow in the middle,
    // approaching/leaving at the ends.
    return clamp01((doppler + span) / (2 * span));
  }

  function trackRGB(trackIndex) {
    // One stable hue per track id (golden-angle spacing); the special
    // TLV 1011 codes (>= 253) and missing associations color dim gray.
    if (trackIndex === null || trackIndex === undefined ||
        trackIndex >= UNASSIGNED_MIN) {
      return [0.36, 0.40, 0.45];
    }
    var hue = (trackIndex * 0.61803398875) % 1;
    return hslToRGB(hue, 0.75, 0.6);
  }

  var api = {
    UNASSIGNED_MIN: UNASSIGNED_MIN,
    readOverlayParams: readOverlayParams,
    boxEdges: boxEdges,
    boresight: boresight,
    fovEdges: fovEdges,
    fovRange: fovRange,
    rampRGB: rampRGB,
    heightT: heightT,
    snrT: snrT,
    dopplerT: dopplerT,
    trackRGB: trackRGB,
  };

  global.LaresOverlays = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
