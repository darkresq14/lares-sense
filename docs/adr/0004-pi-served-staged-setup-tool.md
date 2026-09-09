# ADR-0004: Pi-served staged Setup Tool (config + live view + reader in one process)

- **Status**: Accepted
- **Date**: 2026-09-09
- **Origin**: [#6 — Build live point-cloud + track visualization interface](https://github.com/darkresq14/lares-sense/issues/6) (rescoped after review of the [#5](https://github.com/darkresq14/lares-sense/issues/5) research)
- **Supersedes**: [ADR-0003](0003-websocket-fanout-threejs-web-visualizer.md) (its *where-it-runs* decision; its transport + renderer findings carry over)

## Context

Review of the ADR-0003 research reframed the live visualization: its real job is
**sensor setup at install time**, not an always-on monitoring UI. Viewing recorded
sessions later is a separate, deferred concern (the **Replay Visualizer**).

Physical and codebase constraints load-bearing for this decision:

- **Single UART, blind config** ([ADR-0001](0001-keep-single-uart-reader-on-pi.md)):
  the Pi's only UART to the radar carries config in (115200, no ACK path — CFG_TX
  is not wired) and data out (921600). Whoever sends config must own that UART —
  so a config-writing UI server must live **inside the reader process on the Pi**.
- The deployment reader (`radar_pi/radar_sender.py`) already owns the UART, parses
  TLVs host-side, and UDPs parsed frames to the server.
- The server persist path (ADR-0002) is a dumb UDP consumer and should stay
  untouched.

Requirements settled during the design session (issue #5/#6 thread): a two-stage
model with **no manual restart**, **all** sensor parameters editable with tiered
guidance, local validation (no ACKs to rely on), and the working config persisted
on the Pi across sessions.

## Decision

**One Pi process** — the **Setup Tool host** — owns the UART, serves the web page
(HTTP) and the frame stream (WebSocket), forwards UDP in the Running Stage, and
runs a two-stage state machine:

### Config Stage

- Web editor for **every parameter** of the cfg, tiered:
  *Room & mounting* (boundaryBox, sensorPosition, presence/staticBoundaryBox) —
  "edit at every install"; *Tracking* (gating, allocation, state, maxAcceleration,
  trackingCfg) — "advanced"; *RF/chirp* (profileCfg, chirpCfg, frameCfg, channel,
  antenna, CFAR) — "expert, do not touch unless you know". Defaults = the shipped
  6 m people-tracking cfg; per-param info text sourced from TI CLI/user-guide docs.
- **Live cloud visible** (WS from the same process) overlaid on the room
  preview — tune the box, watch detections fall in/out immediately. **UDP to the
  server is suppressed**, so nothing reaches the DB during tuning.
- **Apply is in-process and blind-verified**: local per-param range validation
  (TI-documented ranges) blocks bad applies; then
  sensorStop → flushCfg → regenerate cfg from Working Config → resend at 115200 →
  sensorStart → back to 921600. Success is inferred only from **frames resuming**
  (ADR-0001: no ACK channel exists); the page surfaces that state.
- One editing session at a time; extra browsers get a read-only view +
  "config locked" banner.

### Running Stage

- Config read-only; page shows a **read-only live monitor** (same WS stream,
  overlays, no edit controls).
- Frames fan out to **both** browsers (WS) and the **server (UDP →
  radar_persist.py)**, which persists as per ADR-0002.
- Entered on "Start" and **automatically on Pi boot when a Working Config
  exists** — a headless site must self-recover after a power blip. Config Stage
  is entered only when no Working Config exists yet, or on a deliberate Stop.

### Working Config

- The **full generated cfg file persisted on the Pi** (e.g. under the reader's
  data dir), not in the repo. The repo keeps only the pristine vendor template
  (`radar_pi/xwr6843_aop_3d_people_track_6m.cfg`). Vendoring the working config
  into the repo is deliberately deferred.

### Rendering

- **three.js**, carried over from ADR-0003's research: `THREE.Points` with
  preallocated `Float32Array` attributes (`DynamicDrawUsage` + `setDrawRange`),
  boundary/presence/static box wireframes, sensor position marker (mount point +
  height + tilt), FOV cone, track labels. Static page served by the Pi process.

This supersedes ADR-0003's "server-served" placement; ADR-0003's transport
(WebSocket push) and renderer (three.js) findings survive unchanged.

## Consequences

**Positive**

- Install flow becomes: open page → tune with live feedback → Start. No manual
  restarts, no reflashing, no hand-editing cfg files on the Pi over SSH.
- DB stays clean during tuning (no throwaway recording sessions).
- Single process owns UART — no IPC seam, no second consumer fighting for the port.
- Headless self-recovery: power blip → boot → auto-Running with the persisted
  Working Config.
- Server side (radar_persist + Timescale) completely untouched.

**Negative**

- The Pi 3B now parses UART, serializes ~18 fps JSON, serves HTTP + WS
  simultaneously. Payload is small (~250 kB/s), but Pi CPU load must be watched.
- Blind apply means validation is **local heuristics only**: a
  range-valid-but-semantically-wrong value can still silently kill the stream;
  the frame-resume check is the only real feedback.
- One process: a web-serving bug can take down the recorder (mitigate by keeping
  the WS hub non-blocking with bounded queues).
- No auth on the page: anyone on the LAN can reconfigure the sensor. Accepted
  for an install-time LAN; revisit before any untrusted-network exposure.

**Deferred**

- **Replay Visualizer** — server-side page scrubbing recorded sessions from the
  DB (start/end select, scroll). Separate issue, deferred; ADR-0003's research is
  its starting material.
- **Vendoring the working config** into the repo.
- Web-page auth.

## Rejected alternatives

- **Server-served UI (ADR-0003 as written)** — wrong host: the config writer must
  be the UART owner on the Pi; a server page cannot send config, and setup would
  couple to server uptime.
- **Desktop GUI (TI's PySide2/pyqtgraph stack)** — per-machine install, no remote
  browser access, re-implements what the Pi process must exist for anyway.
- **Manual restart flow** (edit file on Pi, restart service by hand) — strictly
  worse UX with the same blind-apply risk.
- **JSON overlay for config persistence** — collapses once *all* params are
  editable (overlay ≈ full cfg); the single generated cfg file is one greppable
  artifact, directly what the sensor consumes.
- **Persist frames during Config Stage** — pollutes the DB with tuning sessions;
  suppressed instead.
- **Keep `radar_sender.py` as reader + separate web process** — two processes
  contending for one UART need an IPC seam and gain nothing.

## Related

- [ADR-0001](0001-keep-single-uart-reader-on-pi.md) — single UART, blind config
  (the constraint that forces the process onto the Pi).
- [ADR-0002](0002-one-row-per-frame-timescale-hypertable.md) — persist path the
  Running Stage feeds.
- [ADR-0003](0003-websocket-fanout-threejs-web-visualizer.md) — superseded on
  placement; transport + renderer research reused.
- `radar_pi/radar_sender.py` — the process this tool supersedes as deployment reader.
- [`CONTEXT.md`](../../CONTEXT.md) — Setup Tool / Config Stage / Running Stage /
  Working Config / Replay Visualizer.
