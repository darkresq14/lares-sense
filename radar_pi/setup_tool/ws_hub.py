#!/usr/bin/env python3
"""
Bounded fan-out hub for parsed frames over WebSocket.

Every connected browser gets one asyncio queue; the reader loop pushes each
parsed frame (serialized once) into every queue. Queues are bounded with
drop-oldest overflow so a slow or frozen browser can never stall the reader
(ADR-0004: "the reader loop never blocks on a client").

This module has no socket dependency of its own — the host wires it between
the reader thread and the WebSocket server.
"""

import asyncio
import json


class FrameHub:
    """Fan a parsed-frame dict out to subscribed client queues.

    ``publish``/``publish_threadsafe`` serialize the frame to JSON exactly
    once per frame, then enqueue the same string for every client.
    :meth:`publish_event` does the same for non-frame envelopes (apply
    progress, later stage/health events) and runs on the loop thread. A full
    queue drops its oldest message to make room — a lagging client sees
    skipped frame numbers rather than delaying anyone else.
    """

    def __init__(self, maxsize):
        """``maxsize``: per-client queue depth (the host sets it — one source
        of truth for the bound)."""
        self._maxsize = maxsize
        self._clients = set()

    def subscribe(self):
        """Register a new client; returns its bounded asyncio queue."""
        queue = asyncio.Queue(maxsize=self._maxsize)
        self._clients.add(queue)
        return queue

    def unsubscribe(self, queue):
        """Drop a client (its sender task is ending)."""
        self._clients.discard(queue)

    @property
    def client_count(self):
        return len(self._clients)

    def publish_threadsafe(self, frame, loop):
        """Schedule :meth:`publish` from the reader thread onto ``loop``."""
        if not self._clients:
            return
        loop.call_soon_threadsafe(self.publish, frame)

    def publish(self, frame):
        """Serialize and enqueue ``frame`` for every client.

        The message is an envelope — ``{"type": "frame", "frame": {...}}`` —
        so later stage/health events (same parser-output shape inside) can
        share the stream without a protocol break. Must run on the asyncio
        loop thread (it is what ``publish_threadsafe`` schedules). No-op when
        no client is connected.
        """
        if not self._clients:
            return
        self._enqueue(json.dumps({'type': 'frame', 'frame': frame},
                                 separators=(',', ':')))

    def publish_event(self, kind, **fields):
        """Broadcast one non-frame envelope (e.g. apply progress) to all.

        Same queue and drop-oldest rules as frames — health events ride the
        frame stream (ADR-0004's WS protocol: frames + stage/health events,
        one stream). Runs on the asyncio loop thread.
        """
        if not self._clients:
            return
        self._enqueue(json.dumps({'type': kind, **fields},
                                 separators=(',', ':')))

    def _enqueue(self, message):
        """Put one serialized message on every client queue (drop-oldest)."""
        for queue in list(self._clients):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - raced empty
                    pass
            queue.put_nowait(message)
