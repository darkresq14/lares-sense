#!/usr/bin/env python3
"""
Setup Tool host skeleton (issue #9, ADR-0004).

One process owns the radar's serial device, reads complete TI frames with the
existing reader (``read_radar.read_one_frame``), parses them with the existing
parser (``tlv_parser.parse_frame``), and broadcasts every parsed frame over a
WebSocket. The same port serves the static web page, which renders the live
point cloud with three.js. No config, no stages, no overlays yet — this is the
tracer bullet: open the page from a browser on the LAN and watch points move.

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
import os
import sys
import threading
import time

from read_radar import DATA_BAUD, DEFAULT_PORT as DEFAULT_SERIAL_PORT, read_one_frame
from setup_tool.ws_hub import FrameHub
from tlv_parser import parse_frame

#: Where the static page (index.html, app.js, vendored three.js) lives.
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

#: HTTP + WebSocket share one port (one hole in the Pi's firewall).
DEFAULT_HTTP_PORT = 8000

#: Per-client WS queue depth (frames) before drop-oldest kicks in.
MAX_CLIENT_QUEUE = 64

#: The static routes this skeleton serves. A fixed allow-list — no path
#: traversal possible by construction.
STATIC_FILES = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/vendor/three.min.js': ('vendor/three.min.js',
                             'text/javascript; charset=utf-8'),
}


class SetupToolHost:
    """Serve the Setup Tool page and stream parsed frames over WebSocket.

    Owns two threads on top of the caller's: a blocking serial reader (it
    only touches the injected device and the hub, thread-safely) and an
    asyncio loop running the WebSocket server. The reader never blocks on a
    client — the hub's bounded queues absorb slow browsers.
    """

    def __init__(self, device, bind='0.0.0.0', port=DEFAULT_HTTP_PORT,
                 static_dir=STATIC_DIR, max_client_queue=MAX_CLIENT_QUEUE):
        self._device = device
        self._bind = bind
        self._port = port
        self._static_dir = static_dir
        self._hub = FrameHub(maxsize=max_client_queue)

        self._loop = None
        self._shutdown = None           # asyncio.Event, created on the loop
        self._actual_port = None
        self._ready = threading.Event()

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

    def stop(self):
        """Stop the reader and the server and wait for both threads."""
        self._stop.set()
        try:
            self._device.close()
        except Exception:
            pass

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

    # ---- serial reader thread ----------------------------------------------

    def _reader_loop(self):
        """Blocking loop: read one frame, parse it, hand it to the hub.

        Reuses the existing reader/parser untouched. Exits when the device
        raises (closed / fatal serial error) or the host is stopping.
        """
        while not self._stop.is_set():
            try:
                raw = read_one_frame(self._device)
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
            loop = self._loop
            if loop is not None:
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
        """Per-client pump: drain the hub queue into the socket."""
        queue = self._hub.subscribe()
        try:
            while True:
                message = await queue.get()
                await connection.send(message)
        except Exception:
            pass  # client went away; hub cleanup below
        finally:
            self._hub.unsubscribe(queue)

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
        description='Setup Tool skeleton: serve the live point-cloud page '
                    'and stream parsed radar frames over WebSocket.')
    parser.add_argument('--serial-port', default=DEFAULT_SERIAL_PORT,
                        help=f'Serial device path (default: {DEFAULT_SERIAL_PORT})')
    parser.add_argument('--bind', default='0.0.0.0',
                        help='Interface to serve on (default: 0.0.0.0, '
                             'i.e. reachable from the LAN)')
    parser.add_argument('--port', type=int, default=DEFAULT_HTTP_PORT,
                        help=f'HTTP + WebSocket port (default: '
                             f'{DEFAULT_HTTP_PORT})')
    args = parser.parse_args(argv)

    import serial  # deferred: the host module itself runs device-injected

    print('Lares Setup Tool (skeleton)')
    print(f'  Serial: {args.serial_port} @ {DATA_BAUD} baud')
    print('  Note: the sensor must already be streaming '
          '(configure it once with read_radar.py).')
    print()

    try:
        device = serial.Serial(args.serial_port, DATA_BAUD, timeout=0.6)
    except serial.SerialException as exc:
        print(f'ERROR: Cannot open {args.serial_port}: {exc}')
        sys.exit(1)

    host = SetupToolHost(device, bind=args.bind, port=args.port)
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
