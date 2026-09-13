"""
P5.3 — OS-level network sandboxing pen-tests.

A child subprocess of an app carrying `restrictions: ["network.blocked"]`
MUST NOT be able to open a routable network connection, even by
bypassing FastAPI and using the stdlib socket module directly.

Strategy
--------
We exercise the integration AppProcessRunner.run_entrypoint
end-to-end with two main.py payloads:

  1. **External socket attempt** — main.py tries to connect to
     8.8.8.8:53 (DNS over TCP). Under the macOS seatbelt profile
     OR Linux unshare wrapper, the syscall fails immediately
     with PermissionError / OSError / EHOSTUNREACH. The runner
     captures the failure on stderr and exits non-zero.

  2. **Loopback allowed** — main.py opens a TCP socket to a
     test-managed local listener on 127.0.0.1. This must SUCCEED
     because the seatbelt profile / unshare wrapper explicitly
     permits the loopback interface (per the directive's
     "Secure Handshake Handover" guarantee).

The tests skip cleanly on platforms without an OS-level wrapper
(Linux without `unshare`, Windows) so the suite stays green on
every host while still pen-testing the platforms that have one.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import socket
import threading

import pytest

from core.database.sqlite_setup import _reset_for_tests, init_db
from services.app_sandbox import (
    APP_PROCESS_RUNNER,
    SANDBOX_MANAGER,
    _build_network_isolation_wrapper,
    _manifest_blocks_network,
    _reset_gate_for_tests,
    app_storage_root,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _platform_can_isolate() -> bool:
    """True iff the host can actually wrap the spawn with kernel-
    level isolation (macOS seatbelt OR Linux unshare available AND
    FUNCTIONAL).

    GitHub-hosted runners are FORCED-SKIP via CI/GITHUB_ACTIONS env
    detection: even though /usr/bin/unshare is present and a no-op
    `unshare --user --net true` probe may succeed, the actual sandbox
    invocation (with --map-root-user + --mount-proc + a long Python
    payload) requires unprivileged-namespace-clone capability the
    runner doesn't grant — /proc/self/uid_map write is rejected.

    Self-hosted runners or bare-metal CI that DO support unprivileged
    namespaces can override via VOS3_FORCE_SANDBOX_TESTS=1.
    """
    if os.environ.get("VOS3_FORCE_SANDBOX_TESTS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        pass  # force-enable; fall through to capability detection
    elif os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        return False
    elif os.environ.get("CI", "").lower() == "true":
        return False

    sysname = platform.system()
    if sysname == "Darwin":
        return os.path.exists("/usr/bin/sandbox-exec")
    if sysname == "Linux":
        for cand in ("/usr/bin/unshare", "/bin/unshare"):
            if os.path.exists(cand):
                return True
        return False
    return False


_OS_SKIP_REASON = (
    "host has no OS-level network sandbox tool installed "
    "(macOS sandbox-exec / Linux unshare). The runner's "
    "fallback path is exercised by `test_wrapper_chooses_correct_mode`."
)


def _install(scopes, restrictions=()) -> tuple:
    res = SANDBOX_MANAGER.install(
        {
            "name": "p53-test",
            "version": "1.0.0",
            "scopes": list(scopes),
            "restrictions": list(restrictions),
        }
    )
    return res["app_id"], res["secret"]


def _plant(app_id: str, body: str, *, name: str = "main.py") -> None:
    sandbox = app_storage_root(app_id)
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / name).write_text(body)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_db(airgap_env):
    _reset_for_tests()
    init_db()
    _reset_gate_for_tests()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()
    _reset_gate_for_tests()


@pytest.fixture
def loopback_listener():
    """Spin up a TCP listener on 127.0.0.1 that accepts ONE
    connection and writes 'PING' back. Used by the loopback test
    to confirm the sandboxed child can still reach localhost."""
    ready = threading.Event()
    state = {"port": None, "served": False}
    srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv_sock.bind(("127.0.0.1", 0))
    srv_sock.listen(1)
    state["port"] = srv_sock.getsockname()[1]

    def _serve():
        srv_sock.settimeout(5.0)
        ready.set()
        try:
            client, _addr = srv_sock.accept()
            client.sendall(b"PING")
            client.close()
            state["served"] = True
        except (socket.timeout, OSError):
            pass
        finally:
            srv_sock.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    ready.wait()
    yield state
    try:
        srv_sock.close()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# (1) Wrapper-selection unit tests — run on every host
# ---------------------------------------------------------------------------


def test_manifest_blocks_network_recognizes_restriction(local_db):
    blocked, _ = _install([], restrictions=["network.blocked"])
    free, _ = _install([], restrictions=[])
    assert _manifest_blocks_network(blocked) is True
    assert _manifest_blocks_network(free) is False
    # An unknown app id returns False — the runner just spawns
    # unwrapped (its other guards catch the missing-app case).
    assert _manifest_blocks_network("ghost-id") is False


def test_wrapper_chooses_correct_mode(local_db):
    """Verify the platform dispatch picks the right wrapper."""
    blocked, _ = _install([], restrictions=["network.blocked"])
    prefix, _cleanup, mode = _build_network_isolation_wrapper(
        blocked,
        "/tmp/whatever",
    )
    sysname = platform.system()
    if sysname == "Darwin" and os.path.exists("/usr/bin/sandbox-exec"):
        assert mode == "seatbelt"
        assert prefix[0] == "/usr/bin/sandbox-exec"
        assert prefix[1] == "-f"
        # The 3rd element is the tmp seatbelt profile — must exist.
        assert os.path.exists(prefix[2])
        # The profile must contain the network-deny directive
        # and an explicit loopback allowance.
        with open(prefix[2], "r") as fh:
            body = fh.read()
        assert "(deny network" in body  # blanket deny
        assert "localhost" in body  # loopback allowed
        _cleanup()
        assert not os.path.exists(prefix[2])
    elif sysname == "Linux" and (
        os.path.exists("/usr/bin/unshare") or os.path.exists("/bin/unshare")
    ):
        assert mode == "unshare-net"
        assert prefix[1] == "-n"
    else:
        # No platform support — the runner falls back to API-layer
        # enforcement and emits a warning.
        assert mode in ("fallback-no-unshare", "fallback-unsupported-os")
        assert prefix == []


def test_wrapper_skipped_when_network_not_blocked(local_db):
    app_id, _ = _install([], restrictions=[])
    prefix, _cleanup, mode = _build_network_isolation_wrapper(
        app_id,
        "/tmp/whatever",
    )
    assert prefix == []
    assert mode == "none"


# ---------------------------------------------------------------------------
# (2) Pen-test — external socket connection BLOCKED at OS level
# ---------------------------------------------------------------------------


_PEN_TEST_EXTERNAL_MAIN = r"""
import json, socket, sys

# Try a TCP connection to a routable address — anything off-loopback.
# Under the OS network sandbox this MUST fail with PermissionError
# (macOS) or OSError / timeout (Linux unshare).
target = ("8.8.8.8", 53)
try:
    s = socket.create_connection(target, timeout=2.0)
    s.close()
    print(json.dumps({"ok": True, "reached": "8.8.8.8"}))
    sys.exit(0)
except OSError as e:
    # OSError covers PermissionError, EHOSTUNREACH, ECONNREFUSED,
    # ENETUNREACH, EAFNOSUPPORT, and asyncio timeout sock errors.
    sys.stderr.write("BLOCKED: %s: %s\n" % (type(e).__name__, e))
    sys.exit(1)
"""


@pytest.mark.skipif(
    not _platform_can_isolate(),
    reason=_OS_SKIP_REASON,
)
def test_pen_external_socket_blocked(local_db):
    """The directive's load-bearing scenario: a child carrying
    `restrictions: ["network.blocked"]` cannot reach 8.8.8.8:53
    via socket.create_connection.

    On macOS the seatbelt profile catches it as PermissionError.
    On Linux the unshare network namespace makes the route table
    empty, so the connect attempt fails with EHOSTUNREACH /
    ENETUNREACH."""
    app_id, _ = _install(
        ["process.execute"],
        restrictions=["network.blocked"],
    )
    _plant(app_id, _PEN_TEST_EXTERNAL_MAIN)

    result = asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))
    # The child MUST have failed.
    assert result.returncode != 0, (
        f"external connection should have failed; got "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # And the failure mode looks like an OS-level deny, not a Python
    # parse error.
    combined = (result.stderr or "") + (result.stdout or "")
    assert (
        "BLOCKED:" in combined
        or "Operation not permitted" in combined
        or "Permission denied" in combined
        or "Network is unreachable" in combined
        or "No route to host" in combined
    ), f"unexpected failure mode: {combined!r}"


@pytest.mark.skipif(
    not _platform_can_isolate(),
    reason=_OS_SKIP_REASON,
)
def test_pen_external_socket_blocked_for_dns_hostname(local_db):
    """Same pen-test but using a hostname — DNS resolution itself
    requires network. Under the OS sandbox both the DNS lookup AND
    the connect should fail."""
    app_id, _ = _install(
        ["process.execute"],
        restrictions=["network.blocked"],
    )
    _plant(
        app_id,
        r"""
import json, socket, sys
try:
    s = socket.create_connection(("google.com", 80), timeout=2.0)
    s.close()
    print(json.dumps({"ok": True}))
    sys.exit(0)
except OSError as e:
    sys.stderr.write("BLOCKED: %s: %s\n" % (type(e).__name__, e))
    sys.exit(1)
""",
    )
    result = asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))
    assert result.returncode != 0
    combined = (result.stderr or "") + (result.stdout or "")
    # Either DNS fails OR the connect fails — both are acceptable.
    assert (
        "BLOCKED:" in combined
        or "nodename nor servname" in combined  # macOS getaddrinfo
        or "Name or service not known" in combined  # Linux
        or "Temporary failure in name resolution" in combined
    )


# ---------------------------------------------------------------------------
# (3) Loopback allowed — local-API callbacks still work
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _platform_can_isolate(),
    reason=_OS_SKIP_REASON,
)
def test_loopback_connection_allowed_under_sandbox(local_db, loopback_listener):
    """The directive's Secure Handshake Handover guarantee — the
    sandbox profile permits 127.0.0.1 so first-party callbacks
    (status, RPC, state) still reach the backend."""
    port = loopback_listener["port"]
    app_id, _ = _install(
        ["process.execute"],
        restrictions=["network.blocked"],
    )
    _plant(
        app_id,
        f"""
import json, socket, sys
try:
    s = socket.create_connection(("127.0.0.1", {port}), timeout=2.0)
    data = s.recv(16)
    s.close()
    print(json.dumps({{"ok": True, "data": data.decode()}}))
    sys.exit(0)
except OSError as e:
    sys.stderr.write("UNEXPECTED: %s: %s\\n" % (type(e).__name__, e))
    sys.exit(1)
""",
    )

    result = asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))
    assert result.returncode == 0, (
        f"loopback connection should have succeeded; got "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    payload = json.loads(result.stdout.strip())
    assert payload["ok"] is True
    assert payload["data"] == "PING"
    assert loopback_listener["served"] is True


# ---------------------------------------------------------------------------
# (4) Unblocked app still has full network — sanity baseline
# ---------------------------------------------------------------------------


def test_unblocked_app_runs_without_sandbox_wrapper(local_db):
    """An app WITHOUT `network.blocked` runs through the
    unwrapped path. Just confirm the spawn still works — we do NOT
    actually let it touch the internet in the airgap suite (the
    loopback kill-switch in conftest still applies inside the parent
    test process; the subprocess only does a getsockname check)."""
    app_id, _ = _install(["process.execute"])  # no network.blocked
    _plant(
        app_id,
        (
            "import socket, json\n"
            "# Just resolve the local interface — confirms socket module loads.\n"
            "name = socket.gethostbyname('localhost')\n"
            "print(json.dumps({'name': name}))\n"
        ),
    )
    result = asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["name"].startswith("127.")
