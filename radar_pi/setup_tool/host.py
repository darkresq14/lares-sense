#!/usr/bin/env python3
"""
Setup Tool host (issues #9–#12, ADR-0004).

One process owns the radar's serial device, reads complete TI frames with the
existing reader (``read_radar.read_one_frame``), parses them with the existing
parser (``tlv_parser.parse_frame``), and broadcasts every parsed frame over a
WebSocket. The same port serves the static web page, which renders the live
point cloud with three.js and the tiered config editor (issue #10): every
parameter of the 6 m people-tracking cfg, editable with info text and
inline validation, saved to the Pi-local Working Config. The editor's
**Apply** (issue #11) is blind-verified in-process: sensorStop → flushCfg →
the full regenerated cfg → sensorStart at CLI baud, back to data baud, and
success is inferred only from frames resuming (ADR-0001: the config channel
has no ACK path). A frames-resumed timeout is surfaced as an explicit
apply failure; the editor stays usable. Room preview overlays (issue #13)
— boundary/presence/static wireframe boxes, sensor marker + FOV cone,
track ID labels, color-by — are pure front-end over this same stream
(geometry math in ``static/overlays.js``); overlays preview live from the
editor's DOM values.

Since issue #12 the host runs the full **two-stage machine**:

- **Config Stage** — everything editable, frames reach the browsers, UDP
  to the server is suppressed (tuning never pollutes the DB).
- **Running Stage** — config read-only, frames fan out to both browsers
  (WS) and the server (UDP, raw complete TI frames — the identical wire
  protocol ``radar_sender.py`` uses, so the persist path is untouched).

Since issue #14 the connection side is safe for a crowd: **one editing
session at a time** — the first browser to open the editor owns the
editing lock (``edit_begin`` / ``edit_end``; extra browsers get a
read-only view with a "config locked" banner, and the lock frees when
the editing tab goes away or its panel closes) — and Running Stage turns
the page into a **read-only monitor** anyone on the LAN can watch (edit
controls hidden entirely; the cloud, overlays and tracks stay live).
The WS hub's per-client queues are bounded with drop-oldest overflow and
each sender applies socket backpressure via ``drain()`` — a slow or
frozen browser can never stall the reader loop, the UDP forwarding, or
other clients.

**Start** applies the persisted Working Config and enters Running only
once frames resumed; **Stop** suppresses UDP first, then issues sensorStop
and unlocks the editor. On boot the process auto-enters Running when a
persisted Working Config exists (headless self-recovery after a power
blip); with none it enters Config Stage. Stage and sensor health
(streaming / stopped / reconfiguring / error) ride the same WS stream.

The serial device is injected (duck-typed to ``read(n) -> bytes``, i.e. the
slice of the pyserial API the reader uses), so tests drive the whole host
through a fake device — the single testing seam ADR-0004 settled on.

The sensor must already be streaming: configure it once with
``read_radar.py`` (or any sender), then start this host against the port.

Usage (from radar_pi/):

    python3 -m setup_tool.host --serial-port /dev/serial0 --port 8000

Then open http://<pi-address>:8000/ from any browser on the LAN.
"""

import argparse
import asyncio
import json
import os
import sys
import threading
import time

from read_radar import (CLI_BAUD, DATA_BAUD,
                         DEFAULT_PORT as DEFAULT_SERIAL_PORT, read_one_frame)
from setup_tool.config_model import (
    cfg_command_lines,
    generate_cfg,
    normalize_values,
    parse_cfg,
    schema_json,
)
from setup_tool.config_store import WorkingConfigStore
from setup_tool.udp_forwarder import NullUdpForwarder, UdpForwarder
from setup_tool.ws_hub import FrameHub
from tlv_parser import parse_frame

#: Where the static page (index.html, app.js, vendored three.js) lives.
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

#: HTTP + WebSocket share one port (one hole in the Pi's firewall).
DEFAULT_HTTP_PORT = 8000

#: UDP target of the recording server — the port ``radar_sender.py``
#: streams to and ``radar_persist.py`` listens on.
DEFAULT_UDP_PORT = 5005

#: Per-client WS queue depth (frames) before drop-oldest kicks in.
MAX_CLIENT_QUEUE = 64

#: Seconds to wait for frames to resume after sensorStart before an apply
#: is declared failed (the blind channel's only error detector, ADR-0001).
APPLY_FRAMES_TIMEOUT = 15.0

#: Seconds between cfg command lines while sending (the sensor's CLI parser
#: needs breathing room; ``read_radar.send_config`` uses the same 50 ms).
APPLY_COMMAND_DELAY = 0.05

#: Seconds an apply waits for the reader thread to reach its pause point.
#: Generously above the device read timeout (0.6 s on the real port).
APPLY_PAUSE_TIMEOUT = 5.0

#: Poll cadence while an apply waits (reader parking, frames resuming).
APPLY_POLL_INTERVAL = 0.05

#: The two stages (issue #12; CONTEXT.md fixes the vocabulary).
STAGE_CONFIG = 'config'
STAGE_RUNNING = 'running'

#: Sensor health states (issue #12; the page shows these verbatim).
HEALTH_STOPPED = 'stopped'
HEALTH_RECONFIGURING = 'reconfiguring'
HEALTH_STREAMING = 'streaming'
HEALTH_ERROR = 'error'

#: The static routes this host serves. A fixed allow-list — no path
#: traversal possible by construction.
STATIC_FILES = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/overlays.js': ('overlays.js', 'text/javascript; charset=utf-8'),
    '/vendor/three.min.js': ('vendor/three.min.js',
                             'text/javascript; charset=utf-8'),
}


class _Paused(Exception):
    """Raised inside the reader by the pause-aware device wrapper."""


class _PauseAwareDevice:
    """Reader-side wrapper: every ``read`` first checks the apply pause.

    The reader blocks inside ``read_one_frame``/``find_magic_word`` for as
    long as the line is quiet — exactly the situation during an apply (the
    sensor is stopped). Without this wrapper the reader would never surface
    to notice a pause request. A raised :class:`_Paused` unwinds whatever
    partial frame was in progress; the reader catches it and parks. Writes,
    baud switches and flushes go through the raw device — only ever while
    the reader is parked, so the two never share the port.
    """

    def __init__(self, device, pause):
        self._device = device
        self._pause = pause

    def read(self, size=1):
        if self._pause.is_set():
            raise _Paused()
        return self._device.read(size)


class SetupToolHost:
    """Serve the Setup Tool page and stream parsed frames over WebSocket.

    Owns two threads on top of the caller's: a blocking serial reader (it
    only touches the injected device and the hub, thread-safely) and an
    asyncio loop running the WebSocket server. The reader never blocks on a
    client — the hub's bounded queues absorb slow browsers. On top of the
    stream the loop runs the two-stage machine (issue #12): 'config' /
    'running' × sensor health, entered via Start/Stop and, on boot,
    auto-Running when a Working Config is already persisted.
    """

    def __init__(self, device, bind='0.0.0.0', port=DEFAULT_HTTP_PORT,
                 static_dir=STATIC_DIR, max_client_queue=MAX_CLIENT_QUEUE,
                 config_dir=None, udp_target=None, udp_sink=None,
                 auto_start=None,
                 apply_frames_timeout=APPLY_FRAMES_TIMEOUT,
                 apply_command_delay=APPLY_COMMAND_DELAY):
        self._device = device
        self._bind = bind
        self._port = port
        self._static_dir = static_dir
        self._hub = FrameHub(maxsize=max_client_queue)
        self._apply_frames_timeout = apply_frames_timeout
        self._apply_command_delay = apply_command_delay

        # Working Config: loaded (seeded from the vendor template on first
        # run) from the Pi-local data dir — never the repo. Save/apply touch
        # the serial device only through the validated apply path below,
        # gated by the editing lock (issue #14) in Config Stage.
        self._config_store = WorkingConfigStore(data_dir=config_dir)
        # Boot self-recovery (issue #12): auto-enter Running only when an
        # earlier session persisted a Working Config — so the check runs
        # BEFORE load() seeds the data dir from the template. ``auto_start``
        # overrides the derivation (tests isolate concerns with False).
        if auto_start is None:
            auto_start = self._config_store.exists()
        self._boot_auto_start = bool(auto_start)
        self._config_text, self._config_values = self._load_working_config()

        # Running Stage fan-out: raw complete TI frames, one datagram each,
        # to the recording server (the wire protocol radar_sender uses
        # today). Injectable for tests; never raises into the reader.
        if udp_sink is not None:
            self._udp = udp_sink
        elif udp_target is not None:
            self._udp = UdpForwarder(udp_target)
        else:
            self._udp = NullUdpForwarder()

        # The two-stage machine (issue #12). Stage flips between
        # STAGE_CONFIG and STAGE_RUNNING; health tracks the sensor (the
        # HEALTH_* constants). Both ride the WS stream as
        # {'type': 'stage', ...} events, and every new client gets a
        # snapshot on connect. Loop-thread only (set via the helpers).
        self._stage = STAGE_CONFIG
        self._health = HEALTH_STOPPED

        # The editing lock (issue #14): the connection of the browser
        # that currently owns the single editing session, or None.
        # Taken on edit_begin, freed on edit_end or when that connection
        # goes away. Loop-thread only (see _handle_edit_begin and
        # _handle_connection's finally).
        self._edit_owner = None

        self._loop = None
        self._shutdown = None           # asyncio.Event, created on the loop
        self._actual_port = None
        self._ready = threading.Event()

        # Apply coordination: the reader parks between reads while the
        # apply owns the port (writes at CLI baud, baud switches, flushes).
        self._pause_reading = threading.Event()
        self._reader_idle = threading.Event()
        self._read_device = _PauseAwareDevice(device, self._pause_reading)
        self._applying = False          # loop-thread only (see _apply_config)

        self._stop = threading.Event()
        self._server_thread = None
        self._reader_thread = None

        #: Parsed frames accepted since start (diagnostics for the CLI).
        self.frames_parsed = 0

    # ---- lifecycle ---------------------------------------------------------

    def start(self):
        """Start the WS/HTTP server and the serial reader; returns quickly."""
        self._server_thread = threading.Thread(
            target=self._server_main, name='setup-tool-server', daemon=True)
        self._server_thread.start()

        if not self._ready.wait(timeout=5.0):
            raise RuntimeError('Setup Tool server failed to start')

        self._reader_thread = threading.Thread(
            target=self._reader_loop, name='setup-tool-reader', daemon=True)
        self._reader_thread.start()

        # Boot self-recovery: a persisted Working Config means the site was
        # running before the power blip — re-enter Running headless. With
        # none, the tool stays in Config Stage awaiting first configuration.
        if self._boot_auto_start:
            loop = self._loop
            if loop is not None:
                loop.call_soon_threadsafe(self._kick_auto_start)

    def stop(self):
        """Stop the reader and the server and wait for both threads."""
        self._stop.set()
        try:
            self._device.close()
        except Exception:
            pass
        self._udp.close()

        loop = self._loop
        shutdown = self._shutdown
        if loop is not None and shutdown is not None:
            loop.call_soon_threadsafe(shutdown.set)

        for thread in (self._reader_thread, self._server_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=5.0)

    @property
    def actual_port(self):
        """The bound TCP port (useful when constructed with port=0)."""
        return self._actual_port

    @property
    def client_count(self):
        """Number of connected WS clients."""
        return self._hub.client_count

    @property
    def boot_auto_start(self):
        """Whether a persisted Working Config existed before this boot."""
        return self._boot_auto_start

    @property
    def stage(self):
        """Current stage: 'config' or 'running'."""
        return self._stage

    @property
    def health(self):
        """Current sensor health: stopped / reconfiguring / streaming / error."""
        return self._health

    # ---- serial reader thread ----------------------------------------------

    def _reader_loop(self):
        """Blocking loop: read one frame, parse it, hand it to the hub.

        Reuses the existing reader/parser untouched through the pause-aware
        device wrapper. Exits when the device raises (closed / fatal serial
        error) or the host is stopping; parks while an apply owns the port
        (the ``_Paused`` handshake — see :class:`_PauseAwareDevice`).
        """
        while not self._stop.is_set():
            if self._pause_reading.is_set():
                self._reader_idle.set()
                self._pause_reading.wait(0.1)
                continue
            self._reader_idle.clear()
            try:
                raw = read_one_frame(self._read_device)
            except _Paused:
                continue               # loop top re-checks and parks
            except Exception:
                break  # device closed or fatal — stop() is closing us down
            if raw is None:
                continue  # truncated header; reader resyncs on next magic
            try:
                parsed = parse_frame(raw)
            except Exception as exc:
                # The parser is defensive (returns None for junk), but a
                # surprise must never kill the stream: skip the frame.
                print(f'setup-tool: parser raised ({exc!r}); frame skipped',
                      file=sys.stderr)
                continue
            if parsed is None:
                continue
            self.frames_parsed += 1
            # Running Stage fan-out: the raw complete TI frame, one UDP
            # datagram — identical to radar_sender's wire protocol, so the
            # server-side receiver/persister is untouched (ADR-0004).
            # Config Stage: suppressed — tuning never reaches the DB.
            if self._stage == STAGE_RUNNING:
                self._udp.send(raw)
            loop = self._loop
            if loop is not None:
                # Frames flowing is the sensor's real health signal (the
                # config channel has no ACKs — ADR-0001).
                if self._health != HEALTH_STREAMING:
                    loop.call_soon_threadsafe(self._set_health,
                                               HEALTH_STREAMING)
                self._hub.publish_threadsafe(parsed, loop)

    # ---- WS/HTTP server thread ----------------------------------------------

    def _server_main(self):
        asyncio.run(self._serve())

    async def _serve(self):
        from websockets.asyncio.server import serve

        self._loop = asyncio.get_running_loop()
        self._shutdown = asyncio.Event()

        # compression=None: per-frame deflate would burn Pi CPU for little
        # gain on a ~250 kB/s LAN stream.
        async with serve(self._handle_connection, self._bind, self._port,
                         process_request=self._process_request,
                         compression=None) as server:
            self._actual_port = server.sockets[0].getsockname()[1]
            self._ready.set()
            await self._shutdown.wait()

    async def _handle_connection(self, connection):
        """Per-client pump: drain the hub queue, listen for config requests.

        The same WS stream carries the frame broadcast (``frame``), the
        config editing protocol (``config_get`` / ``config_set`` / ``apply``
        — the WS hub's envelope decision), broadcast apply-progress events,
        the stage machine protocol (issue #12): ``stage`` events for
        stage + sensor health, ``start`` / ``stop`` as requests, and a
        ``stage`` snapshot with ``locked: true`` answering config traffic
        that arrives while Running (the editor is read-only then), and
        the editing lock protocol (issue #14): ``edit_begin`` / ``edit_end``
        requests answered and announced by ``lock`` events — the single
        editing session, held by one browser at a time. The lock frees
        here when its owner's connection goes away. Sender and receiver
        run concurrently so a slow editor request can never stall frame
        delivery for that client.
        """
        queue = self._hub.subscribe()
        # First message: the current stage/health snapshot, so the page
        # knows exactly what it connected to (issue #12's always-visible
        # state) before any frame or transition event arrives.
        await self._send_stage_snapshot(connection)
        if self._edit_owner is not None:
            # Second message for a browser connecting mid-session: the
            # editing lock is held — read-only view with the banner
            # (issue #14). A notice, not a reply: no ``granted`` field.
            await self._send_lock_event(connection, held=True)
        sender = asyncio.create_task(self._client_send_loop(connection, queue))
        try:
            async for message in connection:
                await self._handle_client_message(connection, message)
        except Exception:
            pass  # client went away; hub cleanup below
        finally:
            sender.cancel()
            self._hub.unsubscribe(queue)
            if self._edit_owner is connection:
                # The editing tab went away: the lock frees, and every
                # other browser hears it (issue #14's release criterion).
                self._edit_owner = None
                self._hub.publish_event('lock', held=False)

    async def _client_send_loop(self, connection, queue):
        """Drain the hub queue into the socket (the old pump body).

        Backpressure comes free: websockets' ``send()`` applies the
        transport's flow control, so when a client stops reading and its
        socket buffer passes the high-water mark, this sender parks inside
        ``send`` instead of buffering unboundedly — the client's bounded
        hub queue fills and drops oldest (issue #14's hub hardening; the
        sender stalling is fine, it is per-client).
        """
        try:
            while True:
                message = await queue.get()
                await connection.send(message)
        except (asyncio.CancelledError, Exception):
            return

    def _load_working_config(self):
        """Load the persisted Working Config; recover if it went bad.

        A corrupt or hand-mangled persisted cfg must not brick the tool:
        the broken file is kept aside (``.corrupt`` suffix) and the Working
        Config is re-seeded from the pristine vendor template, loudly.
        """
        text = self._config_store.load()
        try:
            return text, parse_cfg(text)
        except ValueError as exc:
            kept_as = self._config_store.quarantine()
            print(f'setup-tool: persisted Working Config unreadable '
                  f'({exc}); kept as {kept_as}, re-seeded from the '
                  f'vendor template', file=sys.stderr)
            text = self._config_store.load()
            return text, parse_cfg(text)

    async def _handle_client_message(self, connection, message):
        """Dispatch one client JSON message (config + apply protocol).

        Config requests are pure store operations: validate locally,
        persist to the data dir, reply. Apply validates with the same gate
        and then owns the serial port for the blind reconfiguration — see
        :meth:`_apply_config`. Invalid values are rejected before any byte
        could leave in both cases.
        """
        try:
            request = json.loads(message)
        except (TypeError, ValueError):
            return
        kind = request.get('type') if isinstance(request, dict) else None

        if kind in ('config_set', 'apply') and self._stage == STAGE_RUNNING:
            # Running Stage is read-only: the editor is a live monitor.
            await self._send_stage_snapshot(connection, locked=True)
            return

        if kind in ('config_set', 'apply'):
            if self._edit_owner is None:
                # "The first browser to edit owns the session" (issue #14),
                # enforced at the server: a submit that arrives unclaimed
                # claims the lock — silently, so the wire stays quiet for
                # pages that already follow the edit_begin protocol.
                self._edit_owner = connection
            elif self._edit_owner is not connection:
                # Another browser owns the editing session: this one is a
                # read-only view (issue #14). Rejected before any store or
                # serial interaction.
                await self._send_lock_reply(connection, granted=False)
                return

        if kind == 'config_get':
            await connection.send(json.dumps({
                'type': 'config',
                'schema': schema_json(),
                'values': self._config_values,
                'text': self._config_text,
            }))
        elif kind == 'config_set':
            if await self._validate_and_persist(
                    connection, request.get('values') or {}) is None:
                return
        elif kind == 'apply':
            await self._apply_config(connection, request.get('values') or {})
        elif kind == 'start':
            await self._enter_running(connection)
        elif kind == 'stop':
            await self._handle_stop(connection)
        elif kind == 'edit_begin':
            await self._handle_edit_begin(connection)
        elif kind == 'edit_end':
            await self._handle_edit_end(connection)

    # ---- the editing lock (issue #14) -------------------------------------

    async def _send_lock_event(self, connection, held):
        """One ``lock`` event to a single client (notice or broadcast echo)."""
        await connection.send(json.dumps({'type': 'lock', 'held': held},
                                         separators=(',', ':')))

    async def _send_lock_reply(self, connection, granted):
        """Answer one ``edit_begin`` (or a submit from a read-only browser)."""
        await connection.send(json.dumps(
            {'type': 'lock', 'held': True, 'granted': granted},
            separators=(',', ':')))

    async def _handle_edit_begin(self, connection):
        """Claim the single editing session for this browser.

        The first browser to ask in Config Stage wins; later askers get a
        denial and render the read-only view with the "config locked"
        banner. The take is broadcast so every connected browser flips to
        read-only at the same moment. In Running Stage the editor is
        hidden entirely — the stage snapshot (locked) is the answer.
        """
        if self._stage == STAGE_RUNNING:
            await self._send_stage_snapshot(connection, locked=True)
            return
        if self._edit_owner is None:
            self._edit_owner = connection
            await self._send_lock_reply(connection, granted=True)
            self._hub.publish_event('lock', held=True)
        elif self._edit_owner is connection:
            await self._send_lock_reply(connection, granted=True)  # idempotent
        else:
            await self._send_lock_reply(connection, granted=False)

    async def _handle_edit_end(self, connection):
        """Release the editing session (the owner closed its editor panel)."""
        if self._edit_owner is connection:
            self._edit_owner = None
            self._hub.publish_event('lock', held=False)

    # ---- the two-stage machine (issue #12) ---------------------------------

    def _stage_fields(self, locked=False, reason=None):
        """Fields of a ``stage`` event (same shape as the connect snapshot)."""
        fields = {'stage': self._stage, 'health': self._health}
        if locked:
            fields['locked'] = True
        if reason is not None:
            fields['reason'] = reason
        return fields

    async def _send_stage_snapshot(self, connection, locked=False,
                                   reason=None):
        """Answer one client with the current stage/health state."""
        await connection.send(json.dumps(
            {'type': 'stage',
             **self._stage_fields(locked=locked, reason=reason)},
            separators=(',', ':')))

    def _set_stage(self, stage):
        """Flip the stage and broadcast it (loop thread only).

        Entering 'config' also gates the reader's UDP fan-out — from the
        moment the flip lands, tuning frames stop reaching the server.
        """
        if stage != self._stage:
            self._stage = stage
            self._broadcast_stage()

    def _set_health(self, health, reason=None):
        """Update sensor health and broadcast it (loop thread only)."""
        if health == self._health and reason is None:
            return
        self._health = health
        self._broadcast_stage(reason=reason)

    def _broadcast_stage(self, reason=None):
        self._hub.publish_event('stage', **self._stage_fields(reason=reason))

    async def _enter_running(self, connection=None):
        """**Start**: apply the Working Config and enter Running Stage.

        The full blind sequence — sensorStop → flushCfg → the persisted
        cfg → sensorStart at CLI baud → back to data baud — is
        deterministic whether the sensor was streaming or stopped, so
        Start works from a fresh boot, a deliberate stop, or mid-tuning.
        Running (and with it the UDP fan-out) is entered only once frames
        resumed; a timeout leaves the tool in Config Stage with an error
        health and the editor usable. ``connection=None`` is the boot
        self-recovery path: no browser involved, UDP flowing is the
        observable.
        """
        if self._stage == STAGE_RUNNING:
            if connection is not None:
                await self._send_stage_snapshot(connection)
            return
        if self._applying:
            if connection is not None:
                await self._send_stage_snapshot(connection,
                                                reason='another operation '
                                                       'is in progress')
            return
        self._applying = True
        try:
            self._set_health(HEALTH_RECONFIGURING)
            try:
                baseline = await self._own_port_for_config()
            except Exception as exc:  # device-level surprise (seam-tested)
                self._fail_start(f'start error: {exc!r}', connection)
                return
            if not await self._wait_frames_resumed(baseline):
                self._fail_start(
                    f'no frames resumed within '
                    f'{self._apply_frames_timeout:g}s — the sensor may '
                    f'not be streaming', connection)
                return
            self._set_stage(STAGE_RUNNING)
            self._set_health(HEALTH_STREAMING)
        finally:
            self._applying = False

    def _fail_start(self, reason, connection):
        """Fail a Start: error health; headless boot also hits the journal.

        A browser Start shows the reason as a WS ``stage`` event; the boot
        self-recovery path has no page watching, so it also logs one line
        to stderr (visible via ``journalctl`` per the install doc).
        """
        if connection is None:
            print(f'setup-tool: boot auto-enter of Running Stage failed — '
                  f'{reason}', file=sys.stderr)
        self._set_health(HEALTH_ERROR, reason=reason)

    async def _handle_stop(self, connection):
        """**Stop**: back to Config Stage — deliberately.

        UDP is suppressed the moment the stage flips (before any serial
        byte moves), then the sensor is stopped and the editor unlocked.
        """
        if self._stage == STAGE_CONFIG:
            await self._send_stage_snapshot(connection)
            return
        if self._applying:
            await self._send_stage_snapshot(
                connection, reason='another operation is in progress')
            return
        self._applying = True
        try:
            self._set_stage(STAGE_CONFIG)      # UDP suppressed from here
            self._set_health(HEALTH_RECONFIGURING)
            try:
                await self._own_port_for_stop()
            except Exception as exc:
                self._set_health(HEALTH_ERROR, reason=f'stop error: {exc!r}')
                return
            self._set_health(HEALTH_STOPPED)
        finally:
            self._applying = False

    def _kick_auto_start(self):
        """Schedule the headless boot Start on the asyncio loop."""
        asyncio.ensure_future(self._enter_running(connection=None))

    async def _validate_and_persist(self, connection, raw_values):
        """The save half both Save and Apply share (issue #10's gate).

        Validate locally, persist the Working Config, reply. Returns the
        normalized values, or ``None`` after a ``config_rejected`` reply.
        Zero serial interaction — the gate an Apply must pass before any
        byte can leave (ADR-0004).
        """
        values, errors = normalize_values(raw_values)
        if errors:
            await connection.send(json.dumps(
                {'type': 'config_rejected', 'errors': errors}))
            return None
        self._config_text = generate_cfg(values)
        self._config_values = values
        self._config_store.save(self._config_text)
        await connection.send(json.dumps({
            'type': 'config_saved',
            'values': values,
            'text': self._config_text,
        }))
        return values

    async def _apply_config(self, connection, raw_values):
        """Blind apply with frame-resume feedback (issue #11, ADR-0001).

        Validate locally → persist the Working Config → sensorStop →
        flushCfg → the full regenerated cfg → sensorStart, all at CLI baud →
        back to data baud → wait for frames. The config channel has no ACK
        path, so the only success signal is a frame being parsed after the
        sensorStart; every step is broadcast as ``apply_progress`` so the
        page can show "reconfiguring → frames lost → frames resumed ✓".
        A frames-resumed timeout is an explicit failure — the config editor
        stays usable and the port is already back at data baud, so a retry
        works once the sensor actually emits.

        Runs entirely on the asyncio loop (writes are non-blocking buffer
        pushes; pacing uses ``asyncio.sleep``), with the serial reader parked
        for the duration — the two never touch the device concurrently.
        """
        values = await self._validate_and_persist(connection, raw_values)
        if values is None:
            # Invalid config: rejected above, not one byte has left.
            return
        if self._applying:
            self._hub.publish_event('apply_progress', state='failed',
                                    reason='another apply is in progress')
            return
        self._applying = True
        try:
            self._hub.publish_event('apply_progress', state='reconfiguring')
            self._set_health(HEALTH_RECONFIGURING)
            try:
                baseline = await self._own_port_for_config()
            except Exception as exc:  # device-level surprise (seam-tested)
                self._set_health(HEALTH_ERROR, reason=f'apply error: {exc!r}')
                self._hub.publish_event('apply_progress', state='failed',
                                        reason=f'apply error: {exc!r}')
                return

            # Blind channel: frames resuming is the only success signal.
            self._hub.publish_event('apply_progress', state='frames_lost')
            if not await self._wait_frames_resumed(baseline):
                self._set_health(
                    HEALTH_ERROR,
                    reason=f'no frames resumed within '
                           f'{self._apply_frames_timeout:g}s — the config '
                           f'may be bad')
                self._hub.publish_event(
                    'apply_progress', state='failed',
                    reason=f'no frames resumed within '
                           f'{self._apply_frames_timeout:g}s — the config '
                           f'may be bad')
                return
            self._hub.publish_event('apply_progress', state='frames_resumed')
            self._set_health(HEALTH_STREAMING)
        finally:
            self._applying = False

    async def _own_port_for_config(self):
        """Own the port and write the full blind cfg sequence (shared seam).

        sensorStop → flushCfg → the full regenerated cfg → sensorStart, all
        at CLI baud, then the port back to data baud — the exact sequence
        Apply (issue #11), Start and boot self-recovery (issue #12) run.
        Returns the frames-parsed baseline captured **at the park point**:
        once the reader is parked no frame can move the counter, so only
        frames parsed after the sequence count as "resumed" — a straggler
        parsed between the request and the park must not fake success.
        Raises on device failure; callers decide how to surface it.
        """
        lines = [line + '\n' for line in cfg_command_lines(self._config_text)]
        return await self._own_port_writes(lines)

    async def _own_port_for_stop(self):
        """Own the port just long enough to issue sensorStop (Stop's seam).

        Same park/baud/restore discipline as the config sequence. The
        returned baseline goes unused: frames after a stop mean the sensor
        ignored it, and the reader reports those as health on its own.
        """
        return await self._own_port_writes(['sensorStop\n'])

    async def _own_port_writes(self, lines):
        """The one place that owns the port: park the reader, write at CLI
        baud, restore data baud, let the reader resume.

        Pause the reader → wait for its park point → CLI baud → flush →
        write each line (paced) → data baud → flush. A blown write still
        puts the port back at data baud so the reader can see the sensor.
        Returns the frames-parsed baseline at the park point (see
        :meth:`_own_port_for_config`).
        """
        self._pause_reading.set()
        try:
            if not await self._wait_reader_idle():
                raise RuntimeError('reader did not pause — '
                                   'port left untouched')
            baseline = self.frames_parsed
            try:
                self._device.baudrate = CLI_BAUD
                self._device.reset_input_buffer()
                for line in lines:
                    self._device.write(line.encode('ascii'))
                    if self._apply_command_delay:
                        await asyncio.sleep(self._apply_command_delay)
                self._device.baudrate = DATA_BAUD
                self._device.reset_input_buffer()
            except Exception:
                # A blown write must not strand the port at CLI baud —
                # put it back where the reader can see the sensor.
                try:
                    self._device.baudrate = DATA_BAUD
                    self._device.reset_input_buffer()
                except Exception:
                    pass
                raise
        finally:
            self._pause_reading.clear()
        return baseline

    async def _wait_frames_resumed(self, baseline):
        """Wait until a frame parses after ``baseline``; False on timeout."""
        deadline = time.monotonic() + self._apply_frames_timeout
        while self.frames_parsed <= baseline:
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(APPLY_POLL_INTERVAL)
        return True

    async def _wait_reader_idle(self):
        """Non-blocking wait for the reader to reach its pause point."""
        deadline = time.monotonic() + APPLY_PAUSE_TIMEOUT
        while not self._reader_idle.is_set():
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(APPLY_POLL_INTERVAL)
        return True

    def _process_request(self, connection, request):
        """Serve the static page over plain HTTP on the WS port.

        WS handshakes (requests with an ``Upgrade`` header) return ``None``
        and proceed normally; everything else is answered from the static
        allow-list or 404.
        """
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        if 'websocket' in request.headers.get('Upgrade', '').lower():
            return None

        route = STATIC_FILES.get(request.path.split('?', 1)[0])
        if route is None:
            return self._respond(404, 'Not Found', b'not found\n')

        relative, content_type = route
        try:
            with open(os.path.join(self._static_dir, relative), 'rb') as f:
                body = f.read()
        except OSError:
            return self._respond(500, 'Internal Server Error',
                                 b'asset missing\n')

        return self._respond(200, 'OK', body, content_type)

    @staticmethod
    def _respond(status, reason, body, content_type='text/plain; charset=utf-8'):
        """Build a plain-HTTP response served on the WS port."""
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        return Response(status, reason, Headers({
            'Content-Type': content_type,
            'Content-Length': str(len(body)),
            'Cache-Control': 'no-cache',
        }), body)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='python3 -m setup_tool.host',
        description='Setup Tool: live point-cloud page, frame stream over '
                    'WebSocket, tiered config editor with Working Config '
                    'persistence, and blind Apply (sensor restart with '
                    'frame-resume verification).')
    parser.add_argument('--serial-port', default=DEFAULT_SERIAL_PORT,
                        help=f'Serial device path (default: {DEFAULT_SERIAL_PORT})')
    parser.add_argument('--bind', default='0.0.0.0',
                        help='Interface to serve on (default: 0.0.0.0, '
                             'i.e. reachable from the LAN)')
    parser.add_argument('--port', type=int, default=DEFAULT_HTTP_PORT,
                        help=f'HTTP + WebSocket port (default: '
                             f'{DEFAULT_HTTP_PORT})')
    parser.add_argument('--data-dir', default=None,
                        help='Directory for the persisted Working Config '
                             '(default: platform data dir, e.g. '
                             '~/.local/share/lares/setup_tool on the Pi)')
    parser.add_argument('--udp-host', default=os.environ.get('LARES_UDP_HOST'),
                        help='Recording server to forward raw frames to in '
                             'Running Stage (default: $LARES_UDP_HOST, else '
                             'none — frames are not recorded)')
    parser.add_argument('--udp-port', type=int,
                        default=int(os.environ.get('LARES_UDP_PORT',
                                                   DEFAULT_UDP_PORT)),
                        help=f'UDP port on the recording server (default: '
                             f'{DEFAULT_UDP_PORT}, same as radar_sender)')
    args = parser.parse_args(argv)

    import serial  # deferred: the host module itself runs device-injected

    udp_target = None
    if args.udp_host:
        udp_target = (args.udp_host, args.udp_port)

    print('Lares Setup Tool')
    print(f'  Serial: {args.serial_port} @ {DATA_BAUD} baud')
    if udp_target is not None:
        print(f'  UDP (Running Stage): {args.udp_host}:{args.udp_port}')
    else:
        print('  UDP (Running Stage): no server configured — frames will '
              'not be recorded')
    print()

    try:
        device = serial.Serial(args.serial_port, DATA_BAUD, timeout=0.6)
    except serial.SerialException as exc:
        print(f'ERROR: Cannot open {args.serial_port}: {exc}')
        sys.exit(1)

    host = SetupToolHost(device, bind=args.bind, port=args.port,
                         config_dir=args.data_dir, udp_target=udp_target)
    if host.boot_auto_start:
        print('Persisted Working Config found — auto-entering Running '
              'Stage (boot self-recovery).')
    else:
        print('No persisted Working Config — entering Config Stage. The '
              'sensor stays stopped until Apply or Start.')
    try:
        host.start()
    except RuntimeError as exc:
        print(f'ERROR: {exc}')
        device.close()
        sys.exit(1)

    print(f'Serving on http://<this-machine>:{host.actual_port}/ '
          f'(bound to {args.bind})')
    print('Open that URL from a browser on the LAN. Ctrl+C to stop.')
    print()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print('\nStopping...')
    host.stop()
    print(f'Stopped after {host.frames_parsed} parsed frames.')


if __name__ == '__main__':
    main()
