#!/usr/bin/env python3
"""
UDP forwarder for the Running Stage (issue #12, ADR-0004).

In Running Stage every complete raw TI frame goes to the recording server
as one UDP datagram — byte-identical to what ``radar_sender.py`` streams
today, so the receiver/persister path needs zero changes. The forwarder is
a fire-and-forget seam: a UDP failure (server down, LAN hiccup) must never
kill the reader loop, so send errors are swallowed after one loud line.
"""

import socket
import sys


class UdpForwarder:
    """Send raw frame datagrams to one ``(host, port)`` target.

    ``send`` never raises: the reader loop calls it inline per frame, and a
    recording-server outage must not stop the browsers' live view. The
    first failure is logged; afterwards failures stay silent until one
    succeeds again (so a flapping link logs at most once per outage).
    """

    def __init__(self, target):
        self._target = tuple(target)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._failed = False

        #: Datagrams handed to the OS (diagnostics for the CLI).
        self.sent = 0

    def send(self, data):
        """One datagram per complete frame — today's wire protocol."""
        try:
            self._sock.sendto(bytes(data), self._target)
            self.sent += 1
            self._failed = False
        except OSError as exc:
            if not self._failed:
                self._failed = True
                print(f'setup-tool: UDP forward to {self._target[0]}:'
                      f'{self._target[1]} failed ({exc}); frames not '
                      f'recorded until it recovers', file=sys.stderr)

    def close(self):
        self._sock.close()


class NullUdpForwarder:
    """Stand-in when no UDP target is configured.

    Lets a bench run (no recording server) still enter Running Stage; the
    first frame logs once that nothing is being recorded.
    """

    def __init__(self):
        self._warned = False
        self.sent = 0

    def send(self, data):
        self.sent += 1
        if not self._warned:
            self._warned = True
            print('setup-tool: Running Stage without a UDP target '
                  '(--udp-host / LARES_UDP_HOST) — frames are not recorded',
                  file=sys.stderr)

    def close(self):
        pass
