"""
Shared VBus driver fixture for QEMU bridge tests.

QEMU chardev supports only ONE client at a time. This module provides
a single persistent connection shared across all tests in a session.
Import make_driver from here instead of defining per-test.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.vbus_driver import VBusDriver

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")

_shared_driver = None


def make_driver():
    """Get or create a shared VBusDriver connection.

    Reuses a single connection across the entire test session to avoid
    QEMU single-client chardev contention.
    """
    global _shared_driver

    if _shared_driver is not None:
        # Test if still alive
        try:
            resp = _shared_driver.send_command("PING")
            if resp and "ERR" not in resp:
                return _shared_driver
            raise Exception("bad response")
        except Exception:
            try:
                _shared_driver.disconnect()
            except Exception:
                pass
            _shared_driver = None
            time.sleep(0.5)  # Allow QEMU socket to reset

    # Fresh connection with extended retry
    drv = VBusDriver(socket_path=BRIDGE_SOCKET)
    for attempt in range(20):
        if drv.connect():
            _shared_driver = drv
            return drv
        time.sleep(0.5)
    raise RuntimeError("Cannot connect to VOS3 bridge after 20 attempts")
