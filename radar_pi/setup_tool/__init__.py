"""Setup Tool host (ADR-0004).

The Pi-served Setup Tool: one process owns the radar UART, serves the web
page over HTTP, and streams every parsed frame to connected browsers over
a WebSocket on the same port. The page renders the live point cloud with
three.js and carries the tiered config editor (issue #10): every parameter
of the 6 m people-tracking cfg, with info text, documented ranges and
inline validation. Saving persists the Working Config to a Pi-local data
dir — never the repo. Applying config to the sensor (blind apply, stages,
overlays) arrives in later tickets.
"""
