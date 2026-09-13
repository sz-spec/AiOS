"""Lifespan management and service initialization for VOS3 API.

Houses the FastAPI lifespan context manager and the global services dict.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import HTTPException

_startup_logger = logging.getLogger("vos3.startup")

# Global service instances — shared across request handlers
services = {}

# ---------------------------------------------------------------------------
# Auto-Healing: ConvexWriteBuffer health state
# ---------------------------------------------------------------------------
_WRITE_BUFFER_QUEUE_ALERT_THRESHOLD = 5_000
_backpressure_active: bool = False
_backpressure_logger = logging.getLogger("vos3.backpressure")

# Import services (with fallback)
try:
    from core import (
        get_control_plane_service,
        get_business_core_service,
        get_workflow_engine_service,
        get_mission_control_service,
    )
except ImportError as e:
    _startup_logger.warning("Warning: Could not import V-Core services: %s", e)
    get_control_plane_service = lambda: None
    get_business_core_service = lambda: None
    get_workflow_engine_service = lambda: None
    get_mission_control_service = lambda: None

# Import database layer
try:
    from db.convex import get_convex_client

    HAS_DATABASE = True
except ImportError as e:
    _startup_logger.warning("Warning: Could not import database: %s", e)
    HAS_DATABASE = False

# Import authentication middleware
try:
    from middleware.auth import auth_middleware, DEV_MODE as AUTH_DEV_MODE

    HAS_AUTH_MIDDLEWARE = True
except ImportError as e:
    _startup_logger.warning("Warning: Could not import auth middleware: %s", e)
    HAS_AUTH_MIDDLEWARE = False
    AUTH_DEV_MODE = True


async def _convex_health_monitor() -> None:
    """Self-monitoring loop: checks ConvexWriteBuffer queue depth every 30s.

    If the queue exceeds WRITE_BUFFER_QUEUE_ALERT_THRESHOLD:
    - Sends an alert via the monitoring service
    - Activates global backpressure (_backpressure_active = True)

    Backpressure is cleared when queue depth drops below threshold.
    """
    global _backpressure_active
    import asyncio

    while True:
        try:
            await asyncio.sleep(30)
            write_buffer = services.get("write_buffer")
            if write_buffer is None:
                continue

            queue_depth = len(write_buffer._queue)

            if queue_depth > _WRITE_BUFFER_QUEUE_ALERT_THRESHOLD:
                if not _backpressure_active:
                    _backpressure_active = True
                    _backpressure_logger.error(
                        "BACKPRESSURE ACTIVATED: ConvexWriteBuffer queue depth=%d exceeds threshold=%d",
                        queue_depth,
                        _WRITE_BUFFER_QUEUE_ALERT_THRESHOLD,
                    )
                    # Notify monitoring service
                    try:
                        from tools.monitoring import get_monitoring_service

                        mon = get_monitoring_service()
                        if mon and hasattr(mon, "record_error"):
                            mon.record_error(
                                "convex_write_buffer_overflow",
                                f"Queue depth {queue_depth} exceeded threshold {_WRITE_BUFFER_QUEUE_ALERT_THRESHOLD}",
                            )
                    except Exception as _mon_err:
                        _backpressure_logger.warning(
                            "Could not notify monitoring service: %s", _mon_err
                        )
                else:
                    _backpressure_logger.warning(
                        "BACKPRESSURE SUSTAINED: ConvexWriteBuffer queue depth=%d",
                        queue_depth,
                    )
            elif _backpressure_active:
                _backpressure_active = False
                _backpressure_logger.info(
                    "BACKPRESSURE CLEARED: ConvexWriteBuffer queue depth=%d",
                    queue_depth,
                )
        except asyncio.CancelledError:
            break
        except Exception as _err:
            _backpressure_logger.warning("Health monitor error: %s", _err)


@asynccontextmanager
async def lifespan(app):
    """Initialize services on startup, cleanup on shutdown."""
    # Startup
    _startup_logger.info("[*] Starting VOS3 - AI Operating System...")

    # --- VOS_PROFILE dispatcher (Zero-Gap Task 1) ---
    try:
        from vos_profile import get_profile

        _profile = get_profile()
        _startup_logger.info("[+] VOS_PROFILE = %s", _profile.value)
        if _profile.requires_encrypted_compliance_store():
            if not os.environ.get("VOS3_COMPLIANCE_KEY"):
                raise RuntimeError(
                    "VOS_PROFILE=fortress requires VOS3_COMPLIANCE_KEY for SQLCipher."
                )
        services["profile"] = _profile
    except RuntimeError:
        raise
    except ImportError:
        _startup_logger.warning(
            "[!] vos_profile module unavailable — defaulting to community gating"
        )

    # --- P3.2/P3.3 SQLCipher readiness + hybrid self-heal ---
    # Emits a single WARN line if fortress is requested but the host
    # can't satisfy it. When VOS3_AUTO_HEAL=1 is set AND the gap is a
    # missing driver, attempt the automated brew/apt + pip install
    # right here. Operators get either an actionable install command
    # OR a successful auto-install — never a cryptic stack trace on
    # the first encrypted query.
    try:
        from core.database.sqlcipher_setup import (
            attempt_self_heal,
            detect_driver,
            diagnose_readiness,
        )

        _readiness = diagnose_readiness(emit=True)

        if (
            _readiness["profile_requests_fortress"]
            and not _readiness["driver_available"]
        ):
            _startup_logger.warning(
                "[P3.3] Manual fix: %s",
                _readiness.get("fix_command") or "pip install sqlcipher3-binary",
            )
            if (
                _readiness["can_self_heal"]
                and os.environ.get("VOS3_AUTO_HEAL", "").strip() == "1"
            ):
                _startup_logger.warning(
                    "[P3.3] VOS3_AUTO_HEAL=1 — attempting automated "
                    "SQLCipher driver install. This may take several "
                    "minutes on a cold package cache."
                )
                _heal = attempt_self_heal(force=False)
                if _heal.ok:
                    _startup_logger.info(
                        "[+] Self-heal succeeded — sqlcipher3 now importable."
                    )
                    # Re-probe so downstream code sees the driver.
                    _readiness = diagnose_readiness(emit=False)
                else:
                    _startup_logger.error(
                        "[!] Self-heal failed: %s. Run the manual command above.",
                        _heal.error or "unknown error",
                    )

        if _readiness["profile_requests_fortress"] and _readiness["ready"]:
            _startup_logger.info(
                "[+] Fortress profile ready (driver=%s, key=%s)",
                _readiness["driver_module"],
                _readiness["compliance_key_status"],
            )
        services["sqlcipher_readiness"] = _readiness
    except ImportError:
        _startup_logger.debug(
            "[*] sqlcipher_setup not on sys.path — readiness probe skipped"
        )

    # --- Production secrets guard (crash on REPLACE_ME in production) ---
    try:
        from config.secrets import enforce_production_secrets, validate_no_placeholders

        enforce_production_secrets()
        placeholders = validate_no_placeholders()
        if placeholders:
            _startup_logger.warning(
                "[!] Placeholder secrets detected (OK in dev): %s",
                ", ".join(placeholders),
            )
        else:
            _startup_logger.info("[+] All secrets have real values")
    except ImportError:
        _startup_logger.warning(
            "[!] config.secrets not available — skipping production guard"
        )

    # Validate environment configuration (graceful — warnings only)
    try:
        from src.config import validate_config

        _cfg_result = validate_config()
        if not _cfg_result["valid"]:
            for _err in _cfg_result["errors"]:
                _startup_logger.warning("[!] Config warning: %s", _err)
        else:
            _startup_logger.info("[+] Environment configuration validated")
    except Exception as e:
        _startup_logger.warning("[!] Config validation skipped: %s", e)

    # Initialize Convex database connection
    if HAS_DATABASE:
        try:
            db = get_convex_client()
            services["database"] = db
            _startup_logger.info(
                "[+] Database initialized%s", " (dev mode)" if db.dev_mode else ""
            )
        except RuntimeError:
            raise  # Production guard — missing credentials must halt startup
        except Exception as e:
            if os.environ.get("CONVEX_URL"):
                raise RuntimeError(
                    f"Database connection required in production: {e}"
                ) from e
            _startup_logger.warning(
                "[!] Database initialization failed (dev mode, continuing): %s", e
            )

    # Initialize Clerk webhook secret
    _clerk_webhook_secret = os.environ.get("CLERK_WEBHOOK_SECRET")
    _is_production = (
        os.environ.get("VOS3_ENV") == "production"
        or os.environ.get("ENVIRONMENT") == "production"
    )
    if _clerk_webhook_secret:
        try:
            from api.clerk_webhook import set_clerk_webhook_secret

            set_clerk_webhook_secret(_clerk_webhook_secret)
            _startup_logger.info("[+] Clerk webhook secret configured")
        except ImportError:
            pass
    else:
        if _is_production:
            raise RuntimeError(
                "CLERK_WEBHOOK_SECRET is required in production. "
                "Set CLERK_WEBHOOK_SECRET or run with VOS3_ENV=development."
            )
        _startup_logger.warning(
            "[!] CLERK_WEBHOOK_SECRET not set — webhook signature verification disabled (dev mode)"
        )

    # Initialize V-Core services
    try:
        services["control_plane"] = get_control_plane_service()
        services["business_core"] = get_business_core_service()
        services["workflow_engine"] = get_workflow_engine_service()
        services["mission_control"] = get_mission_control_service()
        _startup_logger.info("[+] V-Core services initialized")
    except Exception as e:
        _startup_logger.warning("[!] V-Core services initialization failed: %s", e)

    # Pre-warm LLM factory to avoid cold-start latency on first request
    try:
        from src.efficiency import assign_model  # noqa: F401

        _startup_logger.info("[+] LLM factory pre-warmed")
    except Exception as e:
        _startup_logger.warning("[!] LLM factory pre-warm skipped: %s", e)

    # W2.1c — Ollama daemon probe (non-blocking, 2s timeout, cached)
    try:
        from services.ollama_probe import probe_ollama, get_local_models

        _ollama_ok = probe_ollama()
        services["ollama_available"] = _ollama_ok
        services["ollama_models"] = get_local_models()
        if _ollama_ok:
            _startup_logger.info(
                "[+] Ollama daemon reachable — %d model(s) discovered: %s",
                len(services["ollama_models"]),
                ", ".join(services["ollama_models"][:5]) or "(none listed)",
            )
        else:
            _startup_logger.warning(
                "[!] Ollama daemon unreachable — disabling ollama-provider lane "
                "for this run. Air-gap mode without local LLM will degrade gracefully."
            )
    except Exception as e:
        _startup_logger.warning("[!] Ollama probe skipped: %s", e)
        services["ollama_available"] = False
        services["ollama_models"] = ()

    # W6.1 — under local-first preference, run the more thorough Ollama
    # healthcheck that also verifies the target model is pulled, and
    # publish the result so the dashboard / /health can surface it.
    if os.environ.get("VOS3_LOCALITY_PREFERENCE", "").lower() == "local-first":
        try:
            from services.local_llm import log_ollama_healthcheck

            services["ollama_health"] = log_ollama_healthcheck()
        except Exception as e:
            _startup_logger.warning("[!] W6.1 Ollama healthcheck skipped: %s", e)
            services["ollama_health"] = None

    # Initialize VOS engine
    try:
        from vos.engine import get_vos_engine

        services["vos"] = get_vos_engine()
        _startup_logger.info("[+] VOS engine initialized")
    except Exception as e:
        _startup_logger.warning("[!] VOS engine initialization failed: %s", e)

    # Initialize Agent Registry
    try:
        from vos.engine import get_agent_registry

        services["agent_registry"] = get_agent_registry()
        _startup_logger.info(
            "[+] Agent registry initialized (256 slots, PIDs 8-255 dynamic)"
        )
    except Exception as e:
        _startup_logger.warning("[!] Agent registry initialization failed: %s", e)

    # Initialize Redis checkpointer
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        try:
            from langgraph.checkpoint.redis import RedisSaver

            redis_saver = RedisSaver(redis_url)
            services["redis_checkpointer"] = redis_saver
            _startup_logger.info(
                "[+] Redis checkpointer initialized (%s)",
                redis_url.split("@")[-1] if "@" in redis_url else redis_url,
            )
        except ImportError:
            _startup_logger.warning(
                "[!] langgraph-checkpoint-redis not installed — using in-memory checkpointer"
            )
        except Exception as e:
            _startup_logger.warning(
                "[!] Redis checkpointer initialization failed: %s", e
            )

    # Bootstrap VBus model registry cache
    try:
        from services.vbus_driver import VBusDriver

        VBusDriver.populate_model_cache(
            [
                {"model_id": 0x01, "format": "gguf", "size": 0, "label": "GGUF Model"},
                {
                    "model_id": 0x02,
                    "format": "safetensors",
                    "size": 0,
                    "label": "SafeTensors Model",
                },
                {"model_id": 0x03, "format": "onnx", "size": 0, "label": "ONNX Model"},
            ]
        )
        _startup_logger.info("[+] VBus model registry cache bootstrapped (3 formats)")
    except Exception as e:
        _startup_logger.warning("[!] VBus model cache bootstrap skipped: %s", e)

    # AI services (lazy loading)
    _startup_logger.info("[+] AI services ready (lazy loading)")

    # Observability status
    _langsmith_enabled = os.environ.get("LANGCHAIN_TRACING_V2", "").lower() == "true"
    _langfuse_enabled = bool(os.environ.get("LANGFUSE_SECRET_KEY"))
    if _langsmith_enabled or _langfuse_enabled:
        obs_parts = []
        if _langsmith_enabled:
            project = os.environ.get("LANGCHAIN_PROJECT", "default")
            obs_parts.append(f"LangSmith ({project})")
        if _langfuse_enabled:
            obs_parts.append("Langfuse")
        _startup_logger.info("[+] LLM tracing: %s", ", ".join(obs_parts))

    # Auth status + JWKS cache warming
    if HAS_AUTH_MIDDLEWARE:
        _startup_logger.info(
            "[+] Authentication middleware loaded%s",
            " (dev mode)" if AUTH_DEV_MODE else "",
        )
        try:
            from middleware.auth import init_jwks_cache

            await init_jwks_cache()
            _startup_logger.info("[+] JWKS cache warmed + background refresh started")
        except Exception as e:
            _startup_logger.warning(
                "[!] JWKS cache warming failed (will fetch on demand): %s", e
            )

    # Start auto-healing health monitor
    import asyncio as _asyncio

    _health_monitor_task = _asyncio.create_task(
        _convex_health_monitor(), name="convex-health-monitor"
    )
    _health_monitor_task.add_done_callback(
        lambda t: (
            _backpressure_logger.error("Health monitor task crashed: %s", t.exception())
            if not t.cancelled() and t.exception()
            else None
        )
    )
    _startup_logger.info(
        "[+] ConvexWriteBuffer auto-healing monitor started (threshold=%d)",
        _WRITE_BUFFER_QUEUE_ALERT_THRESHOLD,
    )

    yield

    # Shutdown
    _startup_logger.info("[*] Shutting down VOS3...")

    # Shutdown health monitor
    _health_monitor_task.cancel()
    try:
        import asyncio as _asyncio

        await _asyncio.wait_for(_asyncio.shield(_health_monitor_task), timeout=2.0)
    except Exception:
        pass

    # Shutdown JWKS background refresh
    if HAS_AUTH_MIDDLEWARE:
        try:
            from middleware.auth import shutdown_jwks_cache

            await shutdown_jwks_cache()
            _startup_logger.info("[+] JWKS cache shutdown complete")
        except Exception:
            pass

    if "redis_checkpointer" in services:
        try:
            services["redis_checkpointer"].conn.close()
            _startup_logger.info("[+] Redis checkpointer closed")
        except Exception:
            pass

    if HAS_DATABASE and "database" in services:
        try:
            await services["database"].close()
        except Exception:
            pass


def get_service(name: str):
    """Get a service instance by name."""
    if name not in services:
        raise HTTPException(status_code=503, detail=f"Service {name} not available")
    return services[name]


async def require_capacity() -> None:
    """FastAPI dependency: raise 503 if write-buffer backpressure is active.

    Inject into non-critical routers to shed load automatically:
        router = APIRouter(dependencies=[Depends(require_capacity)])
    """
    if _backpressure_active:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "BACKPRESSURE_ACTIVE",
                "message": "Service temporarily at capacity. Please retry in 30 seconds.",
                "retry_after": 30,
            },
        )
