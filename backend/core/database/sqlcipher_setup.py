"""
backend/core/database/sqlcipher_setup.py — SQLCipher encryption at rest.

P3.2 — encrypted SQLite engine factory for the `VOS_PROFILE=fortress`
profile. Decoupled from sqlite_setup.py so:

  * Plain-SQLite deployments don't pay the import cost of looking
    for an SQLCipher driver that isn't there.
  * The encryption-specific tests can target this module directly
    without touching the path/model fixtures.

Driver detection
----------------
SQLCipher Python bindings have two common names:

  sqlcipher3            — modern (sqlcipher3-binary on PyPI)
  pysqlcipher3.dbapi2   — older / source-built

We probe both, in that order. The first one that imports cleanly is
used as the DBAPI. If neither is present and the operator has asked
for fortress mode, we raise a clear "install one of these" error
rather than silently falling back to plain SQLite (silent fallback
in a fortress profile would be a compliance regression).

Key resolution
--------------
Precedence:
  1. `$VOS3_COMPLIANCE_KEY`  — the canonical operator-provided key.
  2. Dev fallback — a deterministic key derived from the machine ID,
     ONLY when `ENVIRONMENT != "production"`. Loud warning logged.
  3. Production refusal — `ENVIRONMENT=production` + missing key
     raises immediately.

Wrong-key detection
-------------------
SQLCipher does NOT reject a wrong key at open time — it only fails
on the first read of the encrypted header. We force that check at
engine setup by running `SELECT count(*) FROM sqlite_master` inside
the connect-event handler. The DBAPI error gets translated to the
explicit `WrongComplianceKey` exception so callers can render a
clean operator-facing message.

`PRAGMA cipher_compatibility = 4` is set on every connection so the
file format matches modern SQLCipher 4.x. Operators upgrading from
SQLCipher 3.x must do a one-time migration via `sqlcipher` CLI
(see ops doc).
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SqlCipherUnavailable(RuntimeError):
    """Raised when fortress mode is requested but no SQLCipher driver
    can be imported. Carries an actionable install hint."""


class WrongComplianceKey(RuntimeError):
    """Raised when the configured compliance key fails to decrypt an
    existing database file. Operators should re-verify
    $VOS3_COMPLIANCE_KEY before retrying."""


class ComplianceKeyMissing(RuntimeError):
    """Raised in production when $VOS3_COMPLIANCE_KEY is not set."""


# ---------------------------------------------------------------------------
# Profile + driver detection
# ---------------------------------------------------------------------------


def fortress_active() -> bool:
    """True iff the operator has asked for encrypted-at-rest storage.

    Honors two distinct triggers:
      - `VOS_PROFILE=fortress` (recommended; aligns with CLAUDE.md's
        community/fortress/enterprise profile taxonomy).
      - `VOS3_COMPLIANCE_KEY` set non-empty (legacy operator escape
        hatch — encryption switches on as soon as a key is present).

    The OR means an operator who only sets the key (forgetting the
    profile flag) still gets encryption — a strict-safer default
    than silently writing plaintext.
    """
    profile = os.getenv("VOS_PROFILE", "").strip().lower()
    if profile == "fortress":
        return True
    if os.getenv("VOS3_COMPLIANCE_KEY", "").strip():
        return True
    return False


def detect_driver() -> Optional[type]:
    """Return the SQLCipher DBAPI module, or None if unavailable.

    Probes in order:
      1. sqlcipher3            (modern, prebuilt wheel)
      2. pysqlcipher3.dbapi2   (older, source-built)
    """
    try:
        import sqlcipher3 as _drv  # type: ignore

        return _drv
    except ImportError:
        pass
    try:
        from pysqlcipher3 import dbapi2 as _drv  # type: ignore

        return _drv
    except ImportError:
        pass
    return None


def _require_driver():
    """Get the driver or raise SqlCipherUnavailable with an install hint."""
    drv = detect_driver()
    if drv is None:
        raise SqlCipherUnavailable(
            "VOS_PROFILE=fortress requires a SQLCipher Python driver. "
            "Install one of: "
            "`pip install sqlcipher3-binary` (recommended) or "
            "`pip install pysqlcipher3`. "
            "Refusing to fall back to plain SQLite — a fortress profile "
            "with plaintext storage would be a compliance regression."
        )
    return drv


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------


def _machine_id() -> Optional[str]:
    """Best-effort hardware-bound dev-only key seed.

    Linux: /etc/machine-id (systemd-managed).
    macOS: IOPlatformUUID from `ioreg`.
    Windows: HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid via reg.exe.

    Returns None if the platform-specific source is unreadable. The
    fallback is intentionally narrow — it's a "your machine, your
    encryption" pattern that only makes sense for a single-user dev
    workstation. A laptop's `machine-id` is not a secret; treating
    it as one is a dev convenience, not a security claim.
    """
    sysname = platform.system()
    try:
        if sysname == "Linux":
            p = Path("/etc/machine-id")
            if p.is_file():
                return p.read_text().strip()
        elif sysname == "Darwin":
            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).decode("utf-8", errors="replace")
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split("=", 1)[1].strip().strip('"')
        elif sysname == "Windows":
            out = subprocess.check_output(
                [
                    "reg",
                    "query",
                    r"HKLM\SOFTWARE\Microsoft\Cryptography",
                    "/v",
                    "MachineGuid",
                ],
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).decode("utf-8", errors="replace")
            for line in out.splitlines():
                parts = line.strip().split()
                if "MachineGuid" in line and len(parts) >= 3:
                    return parts[-1]
    except Exception as exc:
        logger.debug("machine-id probe failed: %s", exc)
    return None


def _derive_dev_key(seed: str) -> str:
    """Stretch the machine-id into a 64-hex passphrase.

    SHA-256 is fine here — this is a derivation, not a password hash.
    The threat model is "an attacker who has access to the machine
    can also read the DB"; the dev key just stops someone copying
    the file off the machine to mine it elsewhere.
    """
    return hashlib.sha256(("vos3-dev-key|" + seed).encode("utf-8")).hexdigest()


def resolve_compliance_key() -> str:
    """Return the SQLCipher passphrase, or raise.

    Precedence (P6.1 — hardware keyring is now the primary store):
      1. $VOS3_COMPLIANCE_KEY  — explicit operator override
                                  (kept for ops scripts + tests).
      2. Hardware keyring      — `vOS_Secure_Enclave/db_master_key`,
                                  via SovereignKeyringService.
                                  Minted on first boot.
      3. Machine-id dev fallback — last-resort for hosts where the
                                  keyring helper itself crashes.

    Errors:
      ComplianceKeyMissing — production + no env key AND keyring
                              read failed.
    """
    explicit = os.getenv("VOS3_COMPLIANCE_KEY", "").strip()
    if explicit:
        return explicit

    # P6.1 — try the hardware-backed keyring first. The local
    # fallback inside SovereignKeyringService still produces a
    # usable key on hosts without a native backend; an AMBER audit
    # row records the downgrade.
    try:
        from services.crypto_keyring import get_or_mint_db_master_key

        key = get_or_mint_db_master_key()
        if key:
            return key
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[P3.2] keyring lookup failed (%s) — falling back to "
            "machine-id derivation",
            exc,
        )

    env = os.getenv("ENVIRONMENT", "development").strip().lower()
    if env == "production":
        raise ComplianceKeyMissing(
            "VOS3_COMPLIANCE_KEY must be set when VOS_PROFILE=fortress "
            "in production AND the keyring is unreachable. Generate "
            "one with: `python -c 'import secrets; "
            "print(secrets.token_urlsafe(64))'`"
        )

    seed = _machine_id()
    if not seed:
        raise ComplianceKeyMissing(
            "No VOS3_COMPLIANCE_KEY set, keyring unreachable, and "
            "machine-id is unreadable on this host. Set "
            "$VOS3_COMPLIANCE_KEY explicitly."
        )

    key = _derive_dev_key(seed)
    logger.warning(
        "[P3.2] VOS3_COMPLIANCE_KEY not set AND keyring unreachable — "
        "using machine-id-derived dev fallback. Single-user dev ONLY. "
        "Fallback key prefix: %s…",
        key[:12],
    )
    return key


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------


def _escape_key(key: str) -> str:
    """Escape a passphrase for the `PRAGMA key = '...'` literal.

    SQLite doesn't accept parameterized PRAGMAs. Pass the key as a
    literal with single-quotes doubled to escape any internal ones.
    """
    return key.replace("'", "''")


def make_encrypted_engine(db_path: Path, key: Optional[str] = None) -> Engine:
    """Build a SQLAlchemy engine bound to an encrypted SQLite file.

    Parameters
    ----------
    db_path
        Filesystem path to the .db file. Parent dirs are created if
        missing.
    key
        Optional override for the passphrase. If None, uses the
        result of :func:`resolve_compliance_key`.

    Raises
    ------
    SqlCipherUnavailable
        No SQLCipher driver installed.
    WrongComplianceKey
        Existing DB file present but the key doesn't decrypt it.
    """
    driver = _require_driver()
    if key is None:
        key = resolve_compliance_key()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    escaped = _escape_key(key)
    db_path_str = str(db_path)

    def _connect():
        """SQLAlchemy `creator` — build a raw DBAPI connection each
        time the pool needs one. Applies PRAGMA key + probes for a
        wrong-key error immediately, so a stale or rotated key
        surfaces as `WrongComplianceKey` at the connect boundary
        rather than mid-query."""
        conn = driver.connect(db_path_str, check_same_thread=False)
        cur = conn.cursor()
        try:
            cur.execute(f"PRAGMA key = '{escaped}'")
            # Match modern SQLCipher v4 file format.
            cur.execute("PRAGMA cipher_compatibility = 4")
            # Force read of the encrypted header to detect a wrong
            # key. Without this probe, SQLCipher accepts the open
            # silently and fails on the first user query.
            try:
                cur.execute("SELECT count(*) FROM sqlite_master")
                _ = cur.fetchone()
            except Exception as probe_exc:
                # Close cursor first (suppressing errors — the conn
                # may already be wedged) then close conn, then raise.
                # Without the suppress, the finally clause below would
                # try to close an already-closed cursor and mask the
                # real WrongComplianceKey with a ProgrammingError.
                try:
                    cur.close()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                raise WrongComplianceKey(
                    f"Failed to decrypt {db_path_str} with the configured "
                    f"compliance key. The file exists but rejects this "
                    f"key — verify $VOS3_COMPLIANCE_KEY against the "
                    f"value used at creation time. "
                    f"(SQLCipher: {probe_exc.__class__.__name__})"
                ) from probe_exc
            cur.close()
        except WrongComplianceKey:
            raise
        except Exception:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            raise
        return conn

    engine = create_engine(
        "sqlite://",  # URL irrelevant; creator provides conn
        creator=_connect,
        future=True,
    )
    return engine


# ---------------------------------------------------------------------------
# Operator-facing readiness probe — runs at backend startup so a
# missing SQLCipher driver / missing compliance key surfaces with an
# actionable message rather than as a cryptic SQLAlchemy stack trace
# on the first query.
# ---------------------------------------------------------------------------


# Per-platform driver install commands. The "shell" string is what an
# operator would copy/paste into a terminal; the "argv" form is what
# `attempt_self_heal()` executes via subprocess (no shell). Kept as
# parallel forms so the operator-facing hint stays readable while the
# programmatic path stays shell-injection-free.
_DRIVER_INSTALL_PLAN: dict = {
    "Darwin": {
        "shell": (
            "brew install sqlcipher openssl@4 && "
            'CFLAGS="-I$(brew --prefix sqlcipher)/include '
            "-I$(brew --prefix sqlcipher)/include/sqlcipher "
            '-I$(brew --prefix openssl@4)/include" '
            'LDFLAGS="-L$(brew --prefix sqlcipher)/lib '
            '-L$(brew --prefix openssl@4)/lib" '
            "pip install --no-binary :all: sqlcipher3"
        ),
        # argv steps; each runs in sequence, abort on first non-zero.
        "steps": [
            ["brew", "install", "sqlcipher", "openssl@4"],
            # The pip step is wrapped at runtime with the CFLAGS/LDFLAGS
            # discovered from `brew --prefix` so we don't hardcode paths.
        ],
    },
    "Linux": {
        "shell": (
            "apt-get install -y libsqlcipher-dev && "
            "pip install sqlcipher3-binary  "
            "# or: pip install --no-binary :all: sqlcipher3"
        ),
        "steps": [
            ["apt-get", "install", "-y", "libsqlcipher-dev"],
            # pip step injected at runtime by attempt_self_heal.
        ],
    },
    "Windows": {
        "shell": (
            "Install SQLCipher v4 from https://www.zetetic.net/sqlcipher/ "
            "then: pip install sqlcipher3-binary"
        ),
        "steps": [],  # No safe automated path on Windows — manual only.
    },
}


def _platform_fix_plan() -> dict:
    """Return the install plan for the current OS, or a generic stub."""
    return _DRIVER_INSTALL_PLAN.get(
        platform.system(),
        {
            "shell": "pip install sqlcipher3-binary  (or pysqlcipher3)",
            "steps": [],
        },
    )


def diagnose_readiness(*, emit: bool = True) -> dict:
    """Inspect the host and return a structured fortress-readiness report.

    Parameters
    ----------
    emit
        When True (default), write a single-line WARNING to the
        module logger if fortress is requested but the host can't
        deliver it. Returns the report either way.

    Returned dict keys:
      profile_requests_fortress : bool
      driver_available          : bool
      driver_module             : str | None
      compliance_key_status     : "env" | "dev-fallback" | "missing-prod" | "missing-no-machine-id" | "not-needed"
      environment               : str
      ready                     : bool   # True iff fortress + driver + key all aligned
      hints                     : list[str]  # actionable install/setup commands
      fix_command               : str | None  # single copy-pasteable shell line
                                              for the current OS, or None
                                              when no fix is applicable
      can_self_heal             : bool        # True iff attempt_self_heal()
                                              has automated steps for this OS
    """
    profile = fortress_active()
    drv = detect_driver()
    drv_name = getattr(drv, "__name__", None) or (
        drv.__module__ if drv is not None else None
    )
    env = os.getenv("ENVIRONMENT", "development").strip().lower()

    hints: list[str] = []
    fix_command: Optional[str] = None
    can_self_heal: bool = False

    # Driver presence
    if profile and drv is None:
        plan = _platform_fix_plan()
        fix_command = plan["shell"]
        can_self_heal = bool(plan.get("steps"))
        hints.append(fix_command)

    # Key presence
    explicit_key = bool(os.getenv("VOS3_COMPLIANCE_KEY", "").strip())
    if not profile:
        key_status = "not-needed"
    elif explicit_key:
        key_status = "env"
    elif env == "production":
        key_status = "missing-prod"
        hints.append(
            "export VOS3_COMPLIANCE_KEY=\"$(python -c 'import secrets; "
            "print(secrets.token_urlsafe(64))')\""
        )
    elif _machine_id() is None:
        key_status = "missing-no-machine-id"
        hints.append(
            "Set $VOS3_COMPLIANCE_KEY explicitly — machine-id fallback "
            "is unreadable on this host."
        )
    else:
        key_status = "dev-fallback"

    ready = profile and drv is not None and key_status in ("env", "dev-fallback")

    report = {
        "profile_requests_fortress": profile,
        "driver_available": drv is not None,
        "driver_module": drv_name,
        "compliance_key_status": key_status,
        "environment": env,
        "ready": ready,
        "hints": hints,
        "fix_command": fix_command,
        "can_self_heal": can_self_heal,
        "platform": platform.system(),
    }

    if emit and profile and not ready:
        msg_parts = []
        if drv is None:
            msg_parts.append("SQLCipher driver NOT installed")
        if key_status in ("missing-prod", "missing-no-machine-id"):
            msg_parts.append(f"compliance key {key_status}")
        joined = "; ".join(msg_parts) or "unknown gap"
        hint_str = "  Fix: " + " | ".join(hints) if hints else ""
        logger.warning(
            "[P3.2] VOS_PROFILE=fortress requested but host is NOT "
            "fortress-ready — %s.%s",
            joined,
            hint_str,
        )

    return report


# ---------------------------------------------------------------------------
# Hybrid self-heal — opt-in automated install of the SQLCipher driver.
#
# Triggered when:
#   * VOS_PROFILE=fortress is set
#   * detect_driver() returns None
#   * VOS3_AUTO_HEAL=1 is explicitly set OR the caller passes force=True
#
# `attempt_self_heal()` NEVER reads from stdin — it is safe to call
# from an async lifespan hook or a non-interactive process. The
# "interactive prompt" affordance lives in the caller (e.g.
# startup.py), which decides whether to invoke it.
# ---------------------------------------------------------------------------


class SelfHealResult:
    """Structured outcome of an attempt_self_heal() invocation."""

    __slots__ = ("ok", "platform", "steps", "stdout_tail", "error")

    def __init__(
        self,
        *,
        ok: bool,
        platform: str,
        steps: list,
        stdout_tail: str = "",
        error: Optional[str] = None,
    ):
        self.ok = ok
        self.platform = platform
        self.steps = steps
        self.stdout_tail = stdout_tail
        self.error = error

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "platform": self.platform,
            "steps": self.steps,
            "stdout_tail": self.stdout_tail,
            "error": self.error,
        }


def _run_step(argv: list, *, timeout: float, env: Optional[dict] = None) -> tuple:
    """Run an argv list (no shell), return (returncode, output)."""
    try:
        completed = subprocess.run(  # nosec - argv is hardcoded
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        out = (completed.stdout or "") + (completed.stderr or "")
        return completed.returncode, out
    except FileNotFoundError as exc:
        return 127, f"executable not found: {exc}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(argv)}"
    except Exception as exc:  # noqa: BLE001
        return 1, f"{type(exc).__name__}: {exc}"


def attempt_self_heal(
    *,
    force: bool = False,
    timeout_per_step: float = 300.0,
) -> SelfHealResult:
    """Programmatically install the SQLCipher driver on this host.

    Parameters
    ----------
    force
        Bypass the `VOS3_AUTO_HEAL=1` opt-in gate. Tests should pass
        `force=True`; production callers should rely on the env gate
        so a misconfigured host never silently runs `brew install`
        or `apt-get install` without an operator's consent.
    timeout_per_step
        Per-step subprocess timeout, in seconds. The Homebrew/apt
        steps can take a few minutes on a cold cache; the pip build
        from source on macOS is the long pole. Default 5 minutes.

    Returns SelfHealResult with the per-step outcomes. NEVER raises
    for an install failure — the caller inspects `.ok` and logs
    accordingly.
    """
    sysname = platform.system()
    if not force and os.getenv("VOS3_AUTO_HEAL", "").strip() != "1":
        return SelfHealResult(
            ok=False,
            platform=sysname,
            steps=[],
            error=(
                "Self-heal not opted in. Set VOS3_AUTO_HEAL=1 to allow "
                "automated `brew/apt + pip` installation, OR run the "
                "command from diagnose_readiness()['fix_command'] manually."
            ),
        )

    plan = _platform_fix_plan()
    if not plan.get("steps"):
        return SelfHealResult(
            ok=False,
            platform=sysname,
            steps=[],
            error=(
                f"No automated install plan for platform={sysname!r}. "
                f"Run manually: {plan.get('shell', 'pip install sqlcipher3-binary')}"
            ),
        )

    results: list = []
    output_chunks: list = []

    # System package steps (brew / apt) come straight from the plan.
    for argv in plan["steps"]:
        rc, out = _run_step(argv, timeout=timeout_per_step)
        results.append({"argv": argv, "rc": rc})
        output_chunks.append(f"$ {' '.join(argv)}\n{out}")
        if rc != 0:
            return SelfHealResult(
                ok=False,
                platform=sysname,
                steps=results,
                stdout_tail="\n".join(output_chunks)[-2000:],
                error=f"step failed (rc={rc}): {' '.join(argv)}",
            )

    # pip step — composed at runtime so we can splice in the live
    # CFLAGS/LDFLAGS from `brew --prefix` on macOS (avoids hardcoding
    # `/opt/homebrew` vs `/usr/local`).
    pip_env = os.environ.copy()
    if sysname == "Darwin":
        sqlcipher_prefix, _ = _run_step(["brew", "--prefix", "sqlcipher"], timeout=10)
        openssl_prefix_rc, openssl_prefix = _run_step(
            ["brew", "--prefix", "openssl@4"], timeout=10
        )
        if sqlcipher_prefix == 0 and openssl_prefix_rc == 0:
            openssl_prefix.strip()  # placeholder swap below
            # Re-run to capture the actual stdout cleanly.
            sqp = subprocess.run(  # nosec
                ["brew", "--prefix", "sqlcipher"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout.strip()
            osp = subprocess.run(  # nosec
                ["brew", "--prefix", "openssl@4"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout.strip()
            pip_env["CFLAGS"] = (
                f"-I{sqp}/include -I{sqp}/include/sqlcipher -I{osp}/include"
            )
            pip_env["LDFLAGS"] = f"-L{sqp}/lib -L{osp}/lib"
        pip_argv = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--user",
            "--break-system-packages",
            "--no-binary",
            ":all:",
            "sqlcipher3",
        ]
    elif sysname == "Linux":
        # Prefer the prebuilt wheel; fall back to source if no wheel.
        pip_argv = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--user",
            "sqlcipher3-binary",
        ]
    else:
        pip_argv = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--user",
            "sqlcipher3-binary",
        ]

    rc, out = _run_step(pip_argv, timeout=timeout_per_step, env=pip_env)
    results.append({"argv": pip_argv, "rc": rc})
    output_chunks.append(f"$ {' '.join(pip_argv)}\n{out}")
    if rc != 0:
        return SelfHealResult(
            ok=False,
            platform=sysname,
            steps=results,
            stdout_tail="\n".join(output_chunks)[-2000:],
            error=f"pip step failed (rc={rc})",
        )

    # Final verification — can we now import the driver?
    importlib_check = _run_step(
        [sys.executable, "-c", "import sqlcipher3; print(sqlcipher3.__file__)"],
        timeout=10,
    )
    results.append(
        {"argv": ["python", "-c", "import sqlcipher3"], "rc": importlib_check[0]}
    )
    output_chunks.append(f"$ python -c 'import sqlcipher3'\n{importlib_check[1]}")
    if importlib_check[0] != 0:
        return SelfHealResult(
            ok=False,
            platform=sysname,
            steps=results,
            stdout_tail="\n".join(output_chunks)[-2000:],
            error="install completed but `import sqlcipher3` still fails",
        )

    return SelfHealResult(
        ok=True,
        platform=sysname,
        steps=results,
        stdout_tail="\n".join(output_chunks)[-2000:],
    )


__all__ = [
    "SqlCipherUnavailable",
    "WrongComplianceKey",
    "ComplianceKeyMissing",
    "SelfHealResult",
    "fortress_active",
    "detect_driver",
    "resolve_compliance_key",
    "make_encrypted_engine",
    "diagnose_readiness",
    "attempt_self_heal",
]
