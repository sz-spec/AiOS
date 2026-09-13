"""FastAPI application factory for VOS3.

Usage:
    uvicorn backend.app:create_app --factory
    # or via main.py:
    uvicorn main:app --reload --port 8000
"""

import hmac  # W1.2 (REV-2.1) — constant-time API-key comparison
import os
import sys
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)

# Ensure backend is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Central version — single source of truth
VOS3_VERSION = "3.1.0"


def _setup_sentry():
    """Initialize Sentry error tracking if configured."""
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return None
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                StarletteIntegration(transaction_style="endpoint"),
            ],
            traces_sample_rate=float(
                os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")
            ),
            profiles_sample_rate=float(
                os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "0.1")
            ),
            environment=os.environ.get("ENVIRONMENT", "development"),
            release=os.environ.get("VOS3_VERSION", VOS3_VERSION),
            send_default_pii=False,
        )
        print("✅ Sentry error tracking initialized")
        return dsn
    except ImportError:
        print("⚠️ sentry-sdk not installed, error tracking disabled")
    except Exception as e:
        print(f"⚠️ Sentry initialization failed: {e}")
    return None


async def egress_denied_handler(request: Request, exc: Exception) -> JSONResponse:
    """Phase 24 (Gap G1): map a fail-closed EgressDenied to HTTP 403 + a
    [SECURITY] audit record. The precise reason goes to the AUDIT LOG only; the
    client body stays generic (no sensitive-info leak). Module-level + importable
    so the integration tests exercise this exact handler.

    (Audit sink = structured `[SECURITY]` log; the repo has no ClickHouse audit
    subsystem, so this log line IS the audit record a shipper would forward.)"""
    import logging

    reason = getattr(exc, "reason", str(exc))
    path = getattr(getattr(request, "url", None), "path", "<unknown>")
    logging.getLogger("vos3.security.egress").warning(
        "[SECURITY][egress-gate] DENY -> 403 path=%s reason=%s", path, reason
    )
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden", "code": "egress_denied"},
    )


async def m3_gate_rejected_handler(request: Request, exc: Exception) -> JSONResponse:
    """Phase 30 (Gap G9): map a fail-closed M3GateRejected (HSM model-signature
    gate ON + invalid/missing signature) to HTTP 403 + an `M3_GATE_REJECTED`
    audit record. The model_id + reason go to the AUDIT LOG; the client body stays
    generic. Module-level + importable so the integration tests exercise this
    exact handler. (Audit sink = structured `[SECURITY]` log; no ClickHouse.)"""
    import logging

    reason = getattr(exc, "reason", str(exc))
    model_id = getattr(exc, "model_id", "<unknown>")
    path = getattr(getattr(request, "url", None), "path", "<unknown>")
    logging.getLogger("vos3.security.m3_gate").warning(
        "[SECURITY][m3-gate] M3_GATE_REJECTED -> 403 path=%s model=%s reason=%s",
        path,
        model_id,
        reason,
    )
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden", "code": "m3_gate_rejected"},
    )


async def attestation_denied_handler(request: Request, exc: Exception) -> JSONResponse:
    """Phase 31 (Gap G7): map a fail-closed AttestationDenied (no TPM / PCR
    mismatch) to HTTP 403 + a [SECURITY_CRITICAL] audit record. No session token
    is issued on an unattested platform. Module-level + importable so the
    integration tests exercise this exact handler. (Audit sink = structured log;
    no ClickHouse.)"""
    import logging

    reason = getattr(exc, "reason", str(exc))
    path = getattr(getattr(request, "url", None), "path", "<unknown>")
    logging.getLogger("vos3.security.tpm_attestation").critical(
        "[SECURITY_CRITICAL][tpm-attest] ATTESTATION_DENIED -> 403 path=%s reason=%s",
        path,
        reason,
    )
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden", "code": "attestation_denied"},
    )


def create_app() -> FastAPI:
    """Create and configure the VOS3 FastAPI application."""
    from startup import lifespan, services, HAS_AUTH_MIDDLEWARE, AUTH_DEV_MODE
    from router_registry import discover_routers, mount_routers

    sentry_dsn = _setup_sentry()

    application = FastAPI(
        title="VOS3 API",
        description=(
            "VOS3 — AI Operating System for AI-Native Businesses.\n\n"
            "Combines V-Core Business OS, Multi-Agent AI, Cost-Optimized LLM Routing, "
            "and an App Platform for third-party developers."
        ),
        version=VOS3_VERSION,
        lifespan=lifespan,
        openapi_tags=[
            {
                "name": "V-Core",
                "description": "Business OS — organizations, entities, workflows, and monitoring. Provides the core business logic layer with RBAC, custom entity definitions, and workflow automation.",
            },
            {
                "name": "Chat",
                "description": "AI chat with streaming, multi-model support, and smart routing. Supports GPT, Claude, Gemini, and local models with automatic cost optimization.",
            },
            {
                "name": "Code Generation",
                "description": "Code generation from natural language prompts. Supports multiple languages, frameworks, and multi-agent generation with Architect/Frontend/Backend/Tester/Reviewer pipeline.",
            },
            {
                "name": "Agents",
                "description": "Multi-agent orchestration and management. Create, configure, and collaborate with AI agents using templates, RACI matrices, and instruction proposals.",
            },
            {
                "name": "Settings",
                "description": "Runtime configuration for API keys, model preferences, and feature flags.",
            },
            {
                "name": "Voice",
                "description": "Voice control and hands-free interaction with 11-language support.",
            },
            {
                "name": "Metrics",
                "description": "Router observability, cost tracking, and performance metrics.",
            },
            {
                "name": "Memory",
                "description": "Development memory — persistent learning via ChromaDB vector storage.",
            },
            {
                "name": "Billing",
                "description": "Subscription management, credit allocation, and Stripe payment integration.",
            },
            {
                "name": "Teams",
                "description": "Team collaboration, sharing, and member management.",
            },
            {
                "name": "Kernel",
                "description": "VOS3 kernel bridge — process management, filesystem operations, and native code execution via VBus transport.",
            },
            {
                "name": "Plugins",
                "description": "Plugin marketplace — discover, install, and manage third-party extensions.",
            },
            {
                "name": "Apps",
                "description": "App platform — install, manage, and develop apps with scoped API access.",
            },
            {
                "name": "Developers",
                "description": "Developer portal — registration, profiles, app submissions, and earnings analytics.",
            },
            {
                "name": "Terminal",
                "description": "Web-based AI terminal with sandboxed file system access.",
            },
            {
                "name": "BMAD",
                "description": "Business-driven Multi-Agent Development framework.",
            },
        ],
        responses={
            422: {
                "description": "Validation Error",
                "content": {
                    "application/json": {
                        "example": {
                            "error": {
                                "code": "VALIDATION_ERROR",
                                "message": "Request validation failed",
                                "details": {
                                    "errors": [
                                        {
                                            "field": "body -> messages",
                                            "message": "field required",
                                            "type": "missing",
                                        }
                                    ]
                                },
                                "request_id": "550e8400-e29b-41d4-a716-446655440000",
                            }
                        }
                    }
                },
            },
            401: {
                "description": "Unauthorized",
                "content": {
                    "application/json": {
                        "example": {
                            "error": {
                                "code": "UNAUTHORIZED",
                                "message": "Missing authorization token",
                                "request_id": "550e8400-e29b-41d4-a716-446655440000",
                            }
                        }
                    }
                },
            },
            500: {
                "description": "Internal Server Error",
                "content": {
                    "application/json": {
                        "example": {
                            "error": {
                                "code": "INTERNAL_ERROR",
                                "message": "Internal server error",
                                "request_id": "550e8400-e29b-41d4-a716-446655440000",
                            }
                        }
                    }
                },
            },
        },
    )

    # --- Content size limit middleware (outermost — runs before everything) ---
    try:
        from middleware.content_size import content_size_middleware

        application.middleware("http")(content_size_middleware)
    except ImportError:
        pass

    # --- Security-headers middleware (Resilience-Matrix F7) ---
    # Sets Content-Security-Policy + nosniff + frame-deny + Permissions-Policy
    # on every response. Closes the CSP gap raised in the CEO Audit Report.
    try:
        from middleware.security_headers import security_headers_middleware

        application.middleware("http")(security_headers_middleware)
    except ImportError:
        pass

    # --- CORS middleware ---
    # W3.3 — default to Tauri-only origins. The local backend is intended
    # to be reachable only by the Tauri shell:
    #   - tauri://localhost      (production WebView scheme)
    #   - http://localhost:1420  (Tauri dev server)
    # Web browsers running against the same machine cannot use those
    # origins, so they cannot fly under our handshake.
    # `ALLOWED_ORIGINS` env var overrides this when set (legacy browser-
    # dev workflows, CI smoke tests, etc.).
    _origins_env = os.getenv("ALLOWED_ORIGINS", "")
    if _origins_env:
        allowed_origins = [o.strip() for o in _origins_env.split(",") if o.strip()]
    else:
        allowed_origins = ["tauri://localhost", "http://localhost:1420"]
        print(
            "[i] CORS: defaulting to Tauri-only origins (tauri://localhost, http://localhost:1420)"
        )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Api-Key",
            "X-Request-ID",
            # W3.3 — CSRF + handshake headers from the Tauri shell.
            "X-CSRF-Token",
            "X-Tauri-Handshake",
        ],
    )

    # --- CSRF middleware (W3.3 Tauri-IPC firewall) ---
    # Validates X-CSRF-Token on POST/PUT/PATCH/DELETE for /api/*.
    # The token is obtained via GET /api/auth/csrf with a matching
    # X-Tauri-Handshake header — see middleware/csrf.py.
    try:
        from middleware.csrf import csrf_middleware, router as csrf_router

        application.middleware("http")(csrf_middleware)
        application.include_router(csrf_router)
    except ImportError as exc:
        print(f"[!] WARNING: CSRF middleware not loaded: {exc}")

    # W6.3 — public locality/system-status surface. Mounted directly here
    # (instead of router_registry) because it must be reachable before
    # the auth + CSRF middleware list is built, and it has no auth gate.
    try:
        from api.system_routes import router as system_router

        application.include_router(system_router)
    except ImportError as exc:
        print(f"[!] WARNING: system_routes not loaded: {exc}")

    # P6.0 — Sovereign Orchestrator. Bearer-auth-gated inside the
    # router; CSRF gate inherited from the middleware chain above.
    try:
        from api.orchestrator_routes import router as orchestrator_router

        application.include_router(orchestrator_router)
    except ImportError as exc:
        print(f"[!] WARNING: orchestrator_routes not loaded: {exc}")

    # P6.2 — Air-gapped P2P mesh sync. Bearer-auth + workspace-id
    # gating inside the router; rogue requests land in securityAuditLog
    # as `local_tampering_blocked`.
    try:
        from api.p2p_routes import router as p2p_router

        application.include_router(p2p_router)
    except ImportError as exc:
        print(f"[!] WARNING: p2p_routes not loaded: {exc}")

    # P6.3 — Legacy Host Bridge. PermissionGate-checked + Transaction
    # Guard intercept + HITL approval flow before any host subprocess
    # or cloud egress fires.
    try:
        from api.host_bridge_routes import router as host_bridge_router

        application.include_router(host_bridge_router)
    except ImportError as exc:
        print(f"[!] WARNING: host_bridge_routes not loaded: {exc}")

    # P4.1 — Sovereign App Runtime. AppSandboxManager lifecycle ops
    # exposed via /api/apps/*. Mounted at W7.2; was orphan-built but
    # not registered. Bearer-auth gated inside the router. Paired with
    # the AppFileExplorer + AppFileUploadZone components on the dashboard.
    try:
        from api.app_routes import router as app_router

        application.include_router(app_router)
    except ImportError as exc:
        print(f"[!] WARNING: app_routes not loaded: {exc}")

    # --- Response timing middleware ---
    try:
        from middleware.timing import timing_middleware

        application.middleware("http")(timing_middleware)
    except ImportError:
        pass

    # --- Request ID middleware ---
    try:
        from middleware.request_id import request_id_middleware

        application.middleware("http")(request_id_middleware)
    except ImportError:
        pass

    # --- Authentication middleware ---
    if HAS_AUTH_MIDDLEWARE:
        from middleware.auth import auth_middleware

        @application.middleware("http")
        async def authentication_middleware(request: Request, call_next):
            public_paths = [
                "/",
                "/health",
                "/docs",
                "/openapi.json",
                "/redoc",
                "/api/webhooks",
                "/api/billing/webhook",
                "/api/billing/plans",
                # W3.3 — CSRF handshake is gated by X-Tauri-Handshake,
                # not by Bearer. The Tauri shell calls it before sign-in.
                "/api/auth/csrf",
                # W6.3 — locality / system-status posture is public
                # so the frontend can render the sovereign badge
                # before the user authenticates.
                "/api/system/status",
            ]
            path = request.url.path
            is_public = any(path == p or path.startswith(f"{p}/") for p in public_paths)
            if not is_public and not AUTH_DEV_MODE:
                return await auth_middleware(request, call_next)
            return await call_next(request)

    # --- API key middleware ---
    api_secret = os.getenv("VOS_API_SECRET", "")

    # W1.2 (REV-2.1) — refuse to boot with the placeholder default in production.
    # `change_me` is the canonical .env.example placeholder; an empty string is
    # the "no API-key gate configured" signal which is acceptable in dev only.
    _vos_environment = os.getenv("ENVIRONMENT", "")
    if _vos_environment == "production" and api_secret in ("", "change_me"):
        raise RuntimeError(
            "VOS_API_SECRET must be set to a non-default value in production "
            "(refusing to boot with empty or 'change_me' default — see "
            "docs/SECURITY_DEPLOYMENT.md)"
        )
    # Pre-encode for constant-time compare to skip per-request encoding work.
    _api_secret_bytes = api_secret.encode("utf-8") if api_secret else b""

    @application.middleware("http")
    async def api_key_middleware(request: Request, call_next):
        public_paths = ["/", "/health", "/docs", "/openapi.json", "/redoc"]
        webhook_paths = ["/api/webhooks", "/api/billing/webhook"]
        path = request.url.path
        if path in public_paths or not path.startswith("/api"):
            return await call_next(request)
        if any(path == p or path.startswith(f"{p}/") for p in webhook_paths):
            return await call_next(request)
        # When VOS_API_SECRET is not configured, skip API key check entirely.
        # The auth middleware still enforces authentication on all protected routes.
        if not api_secret:
            return await call_next(request)
        api_key = request.headers.get("x-api-key", "")
        # W1.2 (REV-2.1) — constant-time comparison; a non-constant-time `!=`
        # leaks the API key one byte at a time via response-timing.
        api_key_bytes = api_key.encode("utf-8")
        if not hmac.compare_digest(api_key_bytes, _api_secret_bytes):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        return await call_next(request)

    # --- Rate limiting middleware ---
    try:
        from middleware.rate_limit import rate_limit_middleware

        application.middleware("http")(rate_limit_middleware)
    except ImportError:
        pass

    # --- Error reporting helper ---
    def _report_error(request: Request, exc: Exception, sentry_dsn_val):
        if sentry_dsn_val:
            try:
                import sentry_sdk

                sentry_sdk.capture_exception(exc)
            except Exception:
                pass
        try:
            from src.observability import record_error

            source = str(request.url.path)
            error_type = type(exc).__name__
            message = str(exc)
            stack = traceback.format_exc()
            severity = "error"
            if isinstance(exc, HTTPException):
                severity = "error" if exc.status_code >= 500 else "warning"
            record_error(error_type, message, source, severity, stack)
        except Exception as e:
            print(f"Failed to record error: {e}")

    # --- Structured error handlers ---
    from core.errors import VOS3Error, vos3_error_response, ErrorCode
    from core.circuit_breaker import CircuitBreakerOpenError

    @application.exception_handler(CircuitBreakerOpenError)
    async def circuit_breaker_handler(request: Request, exc: CircuitBreakerOpenError):
        # Derive feature name from the Convex function path (e.g. "billing:useTokens" → "billing")
        feature = (
            exc.function_name.split(":")[0] if ":" in exc.function_name else exc.service
        )
        retry_after = max(1, int(exc.retry_after))
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": str(retry_after)},
            content={
                "code": "SERVICE_UNAVAILABLE",
                "service": exc.service,
                "feature": feature,
                "retry_after": retry_after,
                "message": f"The {feature} service is temporarily unavailable. Please retry in {retry_after} seconds.",
                "maintenance_mode": True,
            },
        )

    @application.exception_handler(VOS3Error)
    async def vos3_error_handler(request: Request, exc: VOS3Error):
        _report_error(request, exc, sentry_dsn)
        return vos3_error_response(exc)

    # Phase 24 (Gap G1): fail-closed agent-egress gate -> HTTP 403 + audit.
    from src.efficiency.router import EgressDenied

    application.add_exception_handler(EgressDenied, egress_denied_handler)

    # Phase 30 (Gap G9): fail-closed M3 HSM model-signature gate -> HTTP 403 + audit.
    from services.m3_signature_gate import M3GateRejected

    application.add_exception_handler(M3GateRejected, m3_gate_rejected_handler)

    # Phase 31 (Gap G7): fail-closed TPM platform attestation -> HTTP 403 + audit.
    from services.tpm_attestation import AttestationDenied

    application.add_exception_handler(AttestationDenied, attestation_denied_handler)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        from uuid import uuid4

        req_id = getattr(getattr(request, "state", None), "request_id", None) or str(
            uuid4()
        )
        errors = []
        for err in exc.errors():
            loc = " -> ".join(str(l) for l in err.get("loc", []))
            errors.append(
                {
                    "field": loc,
                    "message": err.get("msg", ""),
                    "type": err.get("type", ""),
                }
            )
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": ErrorCode.VALIDATION_ERROR.value,
                    "message": "Request validation failed",
                    "details": {"errors": errors},
                    "request_id": req_id,
                }
            },
        )

    @application.exception_handler(StarletteHTTPException)
    async def starlette_http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ):
        """Catch all HTTP exceptions including framework-level 404s for unknown routes.

        Produces the structured error envelope ``{"error": {"code", "message", "request_id"}}``
        while also preserving the legacy ``detail`` key for backwards compatibility.
        """
        _report_error(request, exc, sentry_dsn)
        from uuid import uuid4

        req_id = getattr(getattr(request, "state", None), "request_id", None) or str(
            uuid4()
        )
        detail = exc.detail
        detail_str = detail if isinstance(detail, str) else str(detail)
        content = {
            "detail": detail,
            "error": {
                "code": (
                    ErrorCode.INTERNAL_ERROR.value
                    if exc.status_code >= 500
                    else (
                        ErrorCode.NOT_FOUND.value
                        if exc.status_code == 404
                        else (
                            ErrorCode.UNAUTHORIZED.value
                            if exc.status_code == 401
                            else (
                                ErrorCode.FORBIDDEN.value
                                if exc.status_code == 403
                                else "HTTP_ERROR"
                            )
                        )
                    )
                ),
                "message": detail_str,
                "request_id": req_id,
            },
        }
        return JSONResponse(status_code=exc.status_code, content=content)

    @application.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        _report_error(request, exc, sentry_dsn)
        from uuid import uuid4

        req_id = getattr(getattr(request, "state", None), "request_id", None) or str(
            uuid4()
        )
        if isinstance(exc, (HTTPException, StarletteHTTPException)):
            return await starlette_http_exception_handler(request, exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": ErrorCode.INTERNAL_ERROR.value,
                    "message": "Internal server error",
                    "request_id": req_id,
                }
            },
        )

    # --- Discover and mount all routers ---
    routers = discover_routers()
    mount_routers(application, routers)

    # --- Root + Health endpoints ---
    @application.get("/")
    async def root():
        return {
            "name": "VOS3 API",
            "version": VOS3_VERSION,
            "description": "AI Operating System - The Operating System for AI-Native Businesses",
            "modules": {
                "v-core": "/api/v-core - Business OS (orgs, entities, workflows, monitoring)",
                "chat": "/api/chat - AI chat with multi-model support",
                "codegen": "/api/codegen - Code generation from natural language",
                "agents": "/api/agents - Multi-agent orchestration",
                "settings": "/api/settings - Runtime configuration",
                "voice": "/api/voice - Voice control and hands-free interaction",
                "metrics": "/api/metrics - Router observability and cost tracking",
                "memory": "/api/memory - Development memory and learning",
                "bmad": "/api/bmad - Business-driven Multi-Agent Development framework",
                "terminal": "/api/terminal - Web-based AI terminal with file system access",
                "vos": "/api/vos - Hidden system agent (admin observability)",
                "kernel": "/api/kernel - VOS3 kernel bridge (processes, filesystem, execution)",
                "billing": "/api/billing - Subscription and payment management",
                "teams": "/api/teams - Team collaboration and sharing",
                "analytics": "/api/analytics - Business analytics and tracking",
                "github": "/api/github - GitHub sync and version control",
                "inbox": "/api/inbox - Unified communications inbox",
                "templates": "/api/templates - Code template library",
                "plugins": "/api/plugins - Plugin marketplace and management",
                "webhooks": "/api/webhooks - External webhook handlers",
                "apps": "/api/apps/v1 - App platform API (scoped endpoints for third-party apps)",
                "developers": "/api/developers - Developer portal (registration, profiles, apps)",
                "submissions": "/api/apps/submissions - App submission and review workflow",
                "developer_analytics": "/api/developers/analytics - Developer analytics and earnings",
            },
            "docs": "/docs",
        }

    @application.get("/health")
    async def health():
        return {
            "status": "healthy",
            "version": VOS3_VERSION,
            "services": {
                "control_plane": "control_plane" in services,
                "business_core": "business_core" in services,
                "workflow_engine": "workflow_engine" in services,
                "mission_control": "mission_control" in services,
                "redis_checkpointer": "redis_checkpointer" in services,
                "agent_registry": "agent_registry" in services,
            },
        }

    # Phase 38 (Gap G15): audit-readiness lockdown. When VOS3_AUDIT_LOCKDOWN is
    # set, hard-fail boot if any forbidden debug/dump endpoint is mounted. Default
    # OFF -> no-op (no behavior change to the dev/CI matrix).
    from services.lockdown import assert_audit_ready, audit_lockdown_enabled

    if audit_lockdown_enabled():
        assert_audit_ready(application)

    return application
