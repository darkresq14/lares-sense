# ADR-0003: Live visualization — WebSocket fan-out from the persist path, three.js web page served by the server

- **Status**: Superseded by [ADR-0004](0004-pi-served-staged-setup-tool.md) (transport + renderer findings carried over; placement flipped to the Pi)
- **Date**: 2026-09-09
- **Origin**: [#5 — Research how to build a live visualization interface](https://github.com/darkresq14/lares-sense/issues/5)

## Context

Issue #5 asks for a live view of the people-tracking stream that matches the UX of
TI's Industrial Visualizer demo app: a real-time 3D point cloud plus tracked people
(position, per-track color, track IDs). Two decisions are needed:

1. **Live-data transport** — how the UI receives frames as they stream. Each frame is
   a few hundred points (parallel x/y/z/doppler/snr arrays) plus a handful of tracks
   (`radar_pi/tlv_parser.py`); the shipped 6 m people-tracking config
   (`radar_pi/xwr6843_aop_3d_people_track_6m.cfg`) sets a 55 ms frame period
   (`frameCfg 0 2 96 0 55.00 1 0`), i.e. up to ~18 fps — the ~10 fps figure in the
   issue is a safe lower bound, and TI's own 6 m default config uses the same 55 ms
   (`source/ti/examples/.../People_Tracking/3D_People_Tracking/chirp_configs/AOP_6m_default.cfg`
   in the local Radar Toolbox 4.00.00.05 tree).
2. **3D rendering tech** — what renders point cloud + tracks live at that rate.

Hard constraint: **UDP unicast delivers each datagram to one socket**, and
`radar_sender.py` on the Pi sends to a single `host:port`
(`radar_pi/radar_sender.py`, one `sendto` target). Port 5005 is owned by
`radar_persist.py` (writes every frame to Postgres+TimescaleDB, batched 50 rows/1 s
— `radar_pi/frame_store.py`); a second live consumer today would need a second port,
and the README explicitly notes dual consumers are "not currently supported".

**TI visualizer baseline (primary sources).** TI's demo GUI ships in the Radar
Toolbox as the Applications/Industrial Visualizer
([TI Radar Toolbox](https://www.ti.com/tool/RADAR-TOOLBOX);
[user guide on dev.ti.com](https://dev.ti.com/tirex/content/radar_toolbox_1_10_00_13/tools/visualizers/Industrial_Visualizer/docs/mmWave_Industrial_Visualizer_User_Guide.html),
also vendored locally in `docs_and_tools/radar_toolbox_4_00_00_05/`, gitignored).
Verified from its source in the local Toolbox tree:

- It is a **Python desktop GUI**: PySide2 (Qt) app, `python gui_main.py`, whose
  pinned deps are `PySide2`, `pyopengl`, `numpy`, `pyserial`, `pyqtgraph==0.11.0`
  (`tools/visualizers/Applications_Visualizer/requirements.txt`).
- The 3D scene is **pyqtgraph's OpenGL module**: `pyqtgraph.opengl` items —
  `GLScatterPlotItem` for points, `GLLinePlotItem` for the boundary box/track
  geometry, `GLGridItem`, and a custom `GLTextItem` for track-ID labels
  (`common/Common_Tabs/plot_3d.py`, `common/gl_text.py`).
- Data path is **UART → GUI directly** (pyserial; the user guide describes picking
  the CLI/Data COM ports), parsed by `common/parseTLVs.py`, plotted per frame by
  `common/Demo_Classes/people_tracking.py` (which also hard-codes
  `frameTime = 55 ms` and a 30-frame persistent-cloud accumulation).
- The 3D People Tracking user guide confirms the demo "outputs a data stream
  consisting of 3 dimensional point cloud information and a list of tracked objects
  which can be visualized using the PC based visualizer included in the toolbox",
  with per-frame `Num Detected Obj` in the frame header (point count varies per
  frame). Same doc: [3D People Tracking User Guide](https://dev.ti.com/tirex/explore/node?node=A__AJy2dB7pfZ0nJqp9U0xVSg__radar_toolbox__1AslXXD__LATEST).

So the UX bar is: scatter points colored (SNR/height/doppler/track), a 3D bounding
box, per-track IDs and kinematics, ~18 fps updates.

## Decision

**Transport: WebSocket push from the server**, fanned out **in-process from the
existing UDP consumer**. `radar_persist.py` stays the sole owner of UDP :5005; it
already decodes every frame (`parse_frame`), so it hands each parsed frame to both
the batched DB writer *and* a small WebSocket hub (Python
[`websockets`](https://websockets.readthedocs.io/) server on a separate TCP port,
e.g. ws://server:8765). Every connected browser gets each frame the moment it is
decoded — no DB round-trip, no batch-flush delay (the 50-row/1 s batching only
gates persistence, not the live path). A frame serializes to ~10–15 kB of JSON
(few hundred points × ~40 B + a few tracks) at ≤18 fps ≈ ~250 kB/s — trivial for a
WebSocket, which is a single-TCP full-duplex channel designed for exactly this
message-push pattern ([RFC 6455](https://www.rfc-editor.org/rfc/rfc6455);
[MDN: WebSocket API](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket)).

**Renderer: three.js**, served as a static web page **by the server** (same host
that runs the persister — HTTP for the page, WS for frames). Mapping to the TI
visualizer's scene: `THREE.Points` for the cloud (pre-allocated `Float32Array`
position/color attributes; per frame, write arrays, set
`setUsage(THREE.DynamicDrawUsage)` once at setup, flag `needsUpdate = true`, and
adjust `setDrawRange` for the varying point count — the documented in-place
attribute-update pattern,
[three.js BufferAttribute](https://threejs.org/docs/pages/BufferAttribute.html),
[three.js BufferGeometry](https://threejs.org/docs/pages/BufferGeometry.html));
`LineSegments` for the boundary box (analog of `GLLinePlotItem`) and canvas-sprite
labels for track IDs (analog of `GLTextItem`). Orbit-style camera controls give the
drag/zoom 3D view the Qt GUI has.

**Where it runs: on the server.** The Pi (3B) keeps its single job (UART→UDP);
browsers cannot receive UDP anyway, so any web UI needs a relay on a host that
sees the stream — that host is already the server (it owns :5005 and Postgres).

## Consequences

**Positive**

- Zero extra latency: live frames are pushed at decode time; persistence batching
  is untouched and remains crash-durable (ADR-0002).
- No second UDP consumer, no Pi change, no DB load from the live view — respects
  the single-consumer constraint without new ports on the wire.
- One process owns the frame pipeline (receive→decode→persist+broadcast), so the
  UI can never diverge from what is stored.
- Web page = any device with a browser on the LAN; no per-viewer install (the TI
  GUI is a per-desktop Python app).
- three.js gives the exact scene vocabulary the TI demo uses (points, lines, text
  labels, grid) with point-count-varying buffer updates at native GPU speed.

**Negative**

- `radar_persist.py` gains a second responsibility (WS hub); a slow/locked-up
  client must not block the persist loop — the hub needs a bounded send queue /
  drop-on-lag policy (`bufferedAmount` on the browser side,
  [MDN: WebSocket.bufferedAmount](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket/bufferedAmount)).
- Server now also serves HTTP (static page) — one more small service to run and
  firewall.
- WS has no built-in reconnect semantics in browsers (app-level retry needed),
  whereas SSE would auto-reconnect
    ([MDN: Using SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)).
- Reconnect mid-session: a fresh browser gets frames from "now" — backfill of
  recent history (e.g. persistent-cloud trails like TI's 30-frame accumulation)
  needs a small last-N-frames cache in the hub or a DB query.

## Rejected alternatives

Every option from issue #5, each with the fact that sank it:

**Transport**

- **Poll the TimescaleDB** — loses on latency by construction: frames sit in the
  persister's write buffer up to 1 s before the DB sees them (`frame_store.py`
  flush interval), then poll interval is added on top; also a constant queries/sec
  load on the database whether or not frames changed. Fine for history/replay
  (already supported, ADR-0002), wrong for live.
- **Postgres `LISTEN/NOTIFY` as the live feed** — notifications are only delivered
  at transaction commit, so the live path inherits the same batch-commit delay;
  payload must be < 8000 bytes (a full frame's JSON can exceed it), forcing a
  notify→`SELECT` round trip anyway
  ([PostgreSQL NOTIFY docs](https://www.postgresql.org/docs/current/sql-notify.html));
  and it still needs a WS/SSE relay to reach a browser. Two indirections for what
  an in-process fan-out does directly.
- **Direct stream from the Pi (bypass server)** — browsers cannot consume UDP, so
  "direct to UI" is impossible for a web front-end without a relay process anyway;
  `radar_sender.py` supports exactly one target address; and the README/radar
  scripts make dual consumers an unsupported second port. A second `sendto` on the
  Pi (duplicate datagrams to a viz port) is cheap but still needs a WS bridge on
  some host and re-couples the live view to a Pi on the LAN instead of the box
  that already stores everything.
- **SSE (HTTP server-push)** — genuinely viable for this one-way stream and
  auto-reconnects
  ([MDN: Server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events));
  lost narrowly because the event stream is UTF-8 text only (binary point arrays
  would need base64) and non-HTTP/2 SSE has a 6-connections-per-browser limit
  (same MDN page), while WebSocket frames carry binary natively
  ([RFC 6455 §5.6 Data Frames: text is UTF-8, binary arbitrary](https://www.rfc-editor.org/rfc/rfc6455#section-5.6)).

**3D rendering**

- **pyqtgraph/Qt desktop GUI (what TI's own visualizer uses)** — maximum fidelity
  (it is literally the same stack: PySide2 + `pyqtgraph.opengl` per the Toolbox's
  `requirements.txt` and `plot_3d.py`), but it is a per-desktop Python app:
  install + run per viewer, no remote browser access, and it would re-implement or
  duplicate the TI GUI rather than reuse our parsed UDP path. Keep it as the
  bench/offline reference tool, not the product interface.
- **deck.gl** — capable (`PointCloudLayer`), but every `data` change regenerates
  GPU buffers on the CPU — "the most expensive operation that a layer does" — and
  streaming updates require the external-attribute/typed-array machinery to avoid
  stutter ([deck.gl performance guide](https://deck.gl/docs/developer-guide/performance)).
  Heavyweight, geospatially-oriented framework for a few hundred points in a room.
- **plotly.js** — `scatter3d` is WebGL-backed and would technically render this
  scale fine ([plotly.js WebGL docs](https://plotly.com/javascript/webgl-vs-svg/)),
  and incremental APIs exist (`Plotly.restyle`/`extendTraces`,
  [function reference](https://plotly.com/javascript/plotlyjs-function-reference/));
  but updates are figure/trace-level (full x/y/z arrays per frame at 10–18 fps),
  with no per-point GPU-buffer control, a general-charting bundle to ship, and
  awkward custom overlays (track-ID sprites, per-track boxes) compared to a scene
  graph. It wins for quick notebook plots, not for a custom live demo GUI.
- **matplotlib desktop plotter** — redraw-oriented (each frame is a full figure
  redraw; blitting support targets 2D, and `mplot3d` has no streaming buffer
  update API — [matplotlib animation docs](https://matplotlib.org/stable/users/explain/animations/animations.html));
  interactive 3D orbiting at 18 fps with a few hundred points is far outside its
  design target. Worst fit of the four.

**Where it runs**

- **On the Pi (Pi-served web page)** — the Pi would need to host HTTP+WS while
  already doing UART parsing on weak hardware (Pi 3B), and its sender targets one
  address; splits the frame pipeline across two hosts for no benefit.
- **Standalone desktop app on a viewer PC** — reduces to the pyqtgraph option
  above (or a desktop three.js/Electron build, which adds packaging complexity):
  per-machine install and no multi-viewer story.

## Related

Repo:

- `radar_pi/radar_persist.py` — UDP :5005 owner; the fan-out point.
- `radar_pi/frame_store.py` — batched persist path (50 rows/1 s) and schema.
- `radar_pi/tlv_parser.py` — parsed frame shape that the WS message serializes.
- `radar_pi/radar_sender.py` — single-target UDP sender (constraint).
- `docs/adr/0002-one-row-per-frame-timescale-hypertable.md` — storage this UI
  complements; replay/backfill stays a DB concern.
- `README.md` — "Server consumers: either/or" (single UDP consumer per port).
- `docs_and_tools/radar_toolbox_4_00_00_05/tools/visualizers/Applications_Visualizer/`
  — TI visualizer source (local vendor copy; `requirements.txt`, `common/plot_3d.py`,
  `common/gl_text.py`, `common/Demo_Classes/people_tracking.py`).

External primary sources:

- TI Radar Toolbox — <https://www.ti.com/tool/RADAR-TOOLBOX>
- TI mmWave Industrial/Applications Visualizer user guide —
  <https://dev.ti.com/tirex/content/radar_toolbox_1_10_00_13/tools/visualizers/Industrial_Visualizer/docs/mmWave_Industrial_Visualizer_User_Guide.html>
- TI 3D People Tracking user guide —
  <https://dev.ti.com/tirex/explore/node?node=A__AJy2dB7pfZ0nJqp9U0xVSg__radar_toolbox__1AslXXD__LATEST>
- WebSocket: RFC 6455 — <https://www.rfc-editor.org/rfc/rfc6455>;
  MDN WebSocket API — <https://developer.mozilla.org/en-US/docs/Web/API/WebSocket>
- SSE: MDN Using server-sent events —
  <https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events>
- three.js BufferAttribute / BufferGeometry (dynamic attribute updates) —
  <https://threejs.org/docs/pages/BufferAttribute.html>,
  <https://threejs.org/docs/pages/BufferGeometry.html>
- deck.gl performance guide — <https://deck.gl/docs/developer-guide/performance>
- plotly.js WebGL and function reference —
  <https://plotly.com/javascript/webgl-vs-svg/>,
  <https://plotly.com/javascript/plotlyjs-function-reference/>
- PostgreSQL NOTIFY — <https://www.postgresql.org/docs/current/sql-notify.html>
- Python websockets library — <https://websockets.readthedocs.io/>
- matplotlib animations —
  <https://matplotlib.org/stable/users/explain/animations/animations.html>
