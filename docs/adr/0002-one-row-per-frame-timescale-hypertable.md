# ADR-0002: Store tracking frames as one row per frame in a Timescale hypertable

- **Status**: Accepted
- **Date**: 2026-09-08
- **Origin**: [#4 — Persist parsed 3D People Tracking frames to Postgres + TimescaleDB](https://github.com/darkresq14/lares-sense/issues/4)

## Context

Issue #4 asks for durable server-side storage of every parsed 3D People Tracking
frame the Pi publishes over UDP, so historical sessions can be queried and
replayed. Each frame carries: frame number, a point cloud (parallel x/y/z,
doppler, SNR per point), `trackData` (per-track kinematics + 4×4 error
covariance), `heightData` (per-track max_z/min_z) and `trackIndexes` (which
track, if any, owns each point).

PostgreSQL with the TimescaleDB extension is fixed by the issue. Two schema
shapes were considered:

1. **One row per point** (points in a narrow table, tracks in another,
   height/index rows alongside) — relational-normalised, but one 10 fps frame
   with 3 tracks and ~100 points becomes ~100 inserts across 3 tables per
   second, and replaying "frame N" requires re-joining the parts.
2. **One row per frame** — the whole parsed frame in a single row: parallel
   `REAL[]` arrays for the point cloud, JSONB for the variable-structure
   tracker outputs.

## Decision

**One row per frame**, in a single `frames` table converted to a Timescale
**hypertable partitioned on the ingest timestamp `ts`**:

- `ts TIMESTAMPTZ` — ingest time (partition key), `frame_num INTEGER`,
  `num_points INTEGER`.
- `x, y, z, doppler, snr REAL[]` — the point cloud as parallel typed arrays.
- `track_data, height_data JSONB` — `trackData` / `heightData` (variable
  structure: 16-element covariances, per-track dicts).
- `track_indexes SMALLINT[]` — per-point track id (253/254/255 = unassociated).

NULL semantics: a TLV absent from the frame stores NULL in its JSONB /
`track_indexes` column (the parser reports "absent" and "present but empty"
alike, so NULL reads as "no tracking output stored for this frame"); the
point-cloud arrays are always present, possibly empty, so array queries never
meet NULLs.

Writes are batched (one multi-row `INSERT` transaction per batch, flushed on
batch size or a 1 s timer) by `radar_pi/frame_store.py`; the UDP consumer is
`radar_pi/radar_persist.py`.

## Consequences

**Positive**

- One insert per frame keeps the live 10 fps stream trivially ahead of the
  write path; batched writes mean one round-trip per batch, not per frame.
- "Replay frame N" is one row read — no reassembly joins.
- Hypertable partitioning on `ts` gives cheap time-range retention queries and
  later drop-chunk/compression policies without schema change.
- `unnest(x, y, z, doppler, track_indexes)` recovers per-point rows whenever a
  narrow view is wanted; JSONB covers ad-hoc tracker fields.

**Negative**

- Per-point queries must `unnest` arrays (no per-point index); a future
  "detect every point with SNR > X across a week" workload would be slower than
  a normalised points table.
- JSONB stores tracker fields opaque to the planner — filtering on e.g.
  track position uses JSON operators, not typed columns.
- One row per frame wastes nothing today, but a frame with zero points still
  occupies a row (by design: it is evidence the sensor emitted the frame).

**Deferred alternatives (revisit if constraints change)**

- **Per-point normalised tables** — if analytics become point-centric at scale,
  a continuous aggregate or a second normalised table can be added without
  touching the ingest path.
- **Timescale compression policy** — columnar compression (`compress_segmentby`)
  would shrink history cheaply; deferred until retention actually grows.

## Related

- `radar_pi/frame_store.py` — schema (`SCHEMA_SQL`), row mapping, batched writer.
- `radar_pi/radar_persist.py` — UDP consumer.
- `radar_pi/tlv_parser.py` — frame shape feeding `frame_to_row`.
- `README.md` — "Persisting frames (server)" sample queries.
