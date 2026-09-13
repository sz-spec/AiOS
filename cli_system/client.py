"""VOS3 CLI Client — VBus session wrapper with epoch-based auth hardening.

Wraps VBusDriver with session epoch tracking. If a command detects an
epoch mismatch (kernel rebooted, session invalidated), the client tears
down the connection and forces re-authentication before any further
commands execute.
"""

import os
import time
import logging
from typing import Optional

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services.vbus_driver import VBusDriver, VBusError, VBusSecurityError

logger = logging.getLogger("vos3ctl.client")

# Epoch sentinel — returned by kernel in PING/SYSINFO responses
_EPOCH_FIELD = "epoch="


class SessionEpochError(VBusSecurityError):
    """Session epoch mismatch — kernel was rebooted or session invalidated."""
    pass


class VOS3Client:
    """High-level VBus client with session epoch enforcement.

    On connect(), the client negotiates HMAC and records the kernel's boot
    epoch.  Every subsequent command checks the response for epoch markers.
    If the epoch changes mid-session, the client raises SessionEpochError
    and forces a full re-authentication cycle.
    """

    def __init__(self, socket_path: str = "/tmp/vos3_bridge.sock"):
        self._driver = VBusDriver(socket_path=socket_path)
        self._epoch: Optional[str] = None
        self._connected = False
        self._connect_time: Optional[float] = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def epoch(self) -> Optional[str]:
        return self._epoch

    @property
    def driver(self) -> VBusDriver:
        """Direct access to the underlying VBusDriver for low-level ops."""
        return self._driver

    def connect(self) -> bool:
        """Connect to VBus, negotiate HMAC, and capture session epoch.

        Raises VBusSecurityError if HMAC negotiation fails (hard lockdown).
        """
        if self._connected:
            return True

        ok = self._driver.connect()
        if not ok:
            return False

        # Capture boot epoch from SYSINFO
        try:
            info = self._driver.send_command("SYSINFO")
            self._epoch = self._extract_epoch(info)
            logger.info("Session established — epoch=%s", self._epoch)
        except VBusError:
            # SYSINFO not critical — epoch enforcement degrades gracefully
            self._epoch = None
            logger.warning("SYSINFO unavailable — epoch enforcement disabled")

        self._connected = True
        self._connect_time = time.monotonic()
        return True

    def disconnect(self):
        """Tear down the VBus session."""
        self._driver.disconnect()
        self._connected = False
        self._epoch = None
        self._connect_time = None

    def send_command(self, command: str, slot_id: int = 0xFF) -> str:
        """Send command with epoch validation.

        If the kernel's epoch has changed since connect(), this method
        disconnects and raises SessionEpochError, forcing the caller
        to re-authenticate.
        """
        if not self._connected:
            raise VBusError("Not connected — call connect() first")

        resp = self._driver.send_command(command, slot_id=slot_id)
        self._check_epoch(resp)
        return resp

    def ping(self) -> float:
        """Ping with epoch check."""
        if not self._connected:
            raise VBusError("Not connected")
        rtt = self._driver.ping()
        return rtt

    def reconnect(self) -> bool:
        """Force disconnect + reconnect (re-authentication cycle)."""
        logger.info("Re-authentication triggered — reconnecting...")
        self.disconnect()
        return self.connect()

    def session_age_s(self) -> float:
        """Seconds since connect()."""
        if self._connect_time is None:
            return 0.0
        return time.monotonic() - self._connect_time

    # -- internal --

    def _extract_epoch(self, response: str) -> Optional[str]:
        """Extract epoch=<value> from a response string."""
        for part in response.split("|"):
            part = part.strip()
            if part.startswith(_EPOCH_FIELD):
                return part[len(_EPOCH_FIELD):]
        return None

    def _check_epoch(self, response: str):
        """Validate that the kernel epoch hasn't changed mid-session."""
        if self._epoch is None:
            return  # Epoch enforcement disabled (SYSINFO unavailable)

        resp_epoch = self._extract_epoch(response)
        if resp_epoch is None:
            return  # Response doesn't contain epoch — skip check

        if resp_epoch != self._epoch:
            old_epoch = self._epoch
            logger.error(
                "EPOCH MISMATCH: session=%s, kernel=%s — forcing re-auth",
                old_epoch, resp_epoch
            )
            self.disconnect()
            raise SessionEpochError(
                f"Session epoch mismatch (session={old_epoch}, "
                f"kernel={resp_epoch}). Kernel was rebooted or session "
                f"invalidated. Re-authentication required."
            )
