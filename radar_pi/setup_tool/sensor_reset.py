#!/usr/bin/env python3
"""The sensor's hardware reset line (uRAD adapter, RST -> GPIO5).

Measured on the rig (2026-09-12, see the ADR-0004 amendment): the
IWR6843AOP behind the uRAD single-UART adapter never restarts after a
``sensorStop`` issued while streaming — bare ``sensorStart``, a full
``flushCfg`` reconfig and 16 s of patience all stay silent, and the
config channel has no ACK path (ADR-0001), so the wedge is invisible
until frames fail to resume. Pulsing the RST line revives the sensor
from any state; the uRAD IWR-AWR adapter wires it to Raspberry Pi
GPIO5 (pin 29), so the reset is a software operation:

    GPIO5 LOW (>= 0.2 s) -> HIGH   # rising edge resets the sensor

The host injects this line duck-typed (``pulse()`` + ``close()``) the
same way it injects the serial device and the IMU — tests feed a fake
that revives a wedging device model, mirroring the real behavior.
"""

import time

#: GPIO the uRAD adapter wires the sensor RST line to (Pi pin 29).
RESET_GPIO = 5

#: Seconds RST is held LOW — comfortably above any contact bounce; the
#: reset itself triggers on the rising edge (adapter has a pullup).
RESET_LOW_SECONDS = 0.2

#: Seconds the sensor needs after the reset pulse before it accepts cfg
#: lines (boot time measured on the rig: streaming resumed reliably
#: after ~2 s; 2.5 s adds margin without stressing the 15 s
#: frames-resumed budget).
RESET_BOOT_SETTLE = 2.5


class SensorResetLine:
    """The RST line as the host consumes it: ``pulse()`` + ``close()``.

    Constructed once (the pin object stays open); ``pulse()`` blocks for
    ``low_seconds`` on the caller's thread — the host calls it only
    while the serial reader is parked, never in a hot path.
    """

    def __init__(self, gpio=RESET_GPIO,
                 low_seconds=RESET_LOW_SECONDS):
        from gpiozero import OutputDevice   # deferred: dev machines/tests
        self._low_seconds = low_seconds
        self._pin = OutputDevice(gpio, initial_value=True)  # idle HIGH

    @classmethod
    def probe(cls):
        """Return a reset line, or None when GPIO access is unavailable.

        Mirrors :meth:`setup_tool.imu.Mpu9150.probe`: absent gpiozero,
        missing /dev/gpiomem or a dead GPIO library all degrade to None
        — the host then falls back to the legacy sensorStop sequence
        and says so loudly at startup.
        """
        try:
            return cls()
        except Exception:
            return None

    def pulse(self):
        """LOW -> HIGH on RST: the rising edge resets the sensor."""
        self._pin.off()
        time.sleep(self._low_seconds)
        self._pin.on()

    def close(self):
        self._pin.close()
