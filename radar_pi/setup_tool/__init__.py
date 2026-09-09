"""Setup Tool host (ADR-0004).

The Pi-served Setup Tool: one process owns the radar UART, serves the web
page over HTTP, and streams every parsed frame to connected browsers over a
WebSocket on the same port. This package is the skeleton (issue #9): open the
device, read complete TI frames, parse, broadcast, render. Stages, config
editing and overlays arrive in later tickets.
"""
