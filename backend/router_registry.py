"""Router discovery and mounting for VOS3 API.

Centralizes all router imports (fail-safe) and mounting logic
extracted from main.py for maintainability.
"""

import importlib
import os


def _is_production() -> bool:
    """Check if we are running in production mode."""
    return (
        os.environ.get("VOS3_ENV") == "production"
        or os.environ.get("ENVIRONMENT") == "production"
    )


def _try_import_router(module_path: str, label: str, *, strict: bool = False):
    """Import a router module, returning None on failure.

    Args:
        module_path: Dotted Python module path (e.g. "api.chat_routes").
        label: Human-readable label for log messages.
        strict: If True, re-raise ImportError instead of swallowing it.
                Enabled automatically in production to surface broken routes
                at startup rather than silently degrading.
    """
    try:
        mod = importlib.import_module(module_path)
        return mod.router
    except ImportError as e:
        if strict:
            raise ImportError(
                f"[STRICT] Failed to import route module {label} ({module_path}): {e}. "
                f"All route modules must be importable in production."
            ) from e
        print(f"Warning: Could not import {label}: {e}")
        return None
    except Exception as e:
        if strict:
            raise
        print(f"Warning: Could not import {label}: {e}")
        return None


def discover_routers() -> dict:
    """Discover all available routers. Returns {name: router_or_None}.

    In production (VOS3_ENV=production or ENVIRONMENT=production), import
    failures raise immediately instead of being swallowed.  This ensures
    no route is silently missing from a production deployment.
    """
    strict = _is_production()
    r = {}

    # Core routers (via _try_import_router)
    r["v_core"] = _try_import_router(
        "api.v_core_routes", "v_core_routes", strict=strict
    )
    r["chat"] = _try_import_router("api.chat_routes", "chat_routes", strict=strict)
    r["codegen"] = _try_import_router(
        "api.codegen_routes", "codegen_routes", strict=strict
    )
    r["agents"] = _try_import_router(
        "api.agents_routes", "agents_routes", strict=strict
    )
    r["settings"] = _try_import_router(
        "api.settings_routes", "settings_routes", strict=strict
    )
    r["voice"] = _try_import_router("api.voice_routes", "voice_routes", strict=strict)
    r["metrics"] = _try_import_router(
        "api.metrics_routes", "metrics_routes", strict=strict
    )
    r["memory"] = _try_import_router(
        "api.memory_routes", "memory_routes", strict=strict
    )

    # Additional routers (Phase 1: Connect orphaned routes)
    for name, mod in [
        ("billing", "api.billing_routes"),
        ("team", "api.team_routes"),
        ("analytics", "api.analytics_routes"),
        ("github", "api.github_sync_routes"),
        ("inbox", "api.inbox_routes"),
        ("template", "api.template_routes"),
        ("plugin", "api.plugin_routes"),
        ("clerk", "api.clerk_webhook"),
        ("bmad", "api.bmad_routes"),
        ("terminal", "api.terminal_routes"),
        ("vos", "api.vos_routes"),
        ("kernel", "api.kernel_routes"),
        ("files", "api.files"),
        # Day 11: Mount previously orphaned routes
        ("aiva", "api.aiva_routes"),
        ("proactive", "api.proactive_routes"),
        ("prompt", "api.prompt_routes"),
        ("version_control", "api.version_control_routes"),
        ("feature_flags", "api.feature_flag_routes"),
        # Day 12 (REV-2.2): Audited orphan routers — functional code, never wired.
        ("blueprints", "api.blueprints_routes"),
        ("comments", "api.comment_routes"),
        ("expert", "api.expert_routes"),
        ("gateway", "api.gateway_routes"),
        # Sprint 14.1 (Gap 1): Stage-10 compliance routes — Article 73
        ("compliance", "api.compliance_routes"),
        # Phase 26 (Gap G3): policy transparency Merkle proof surface
        ("transparency", "api.transparency_routes"),
    ]:
        r[name] = _try_import_router(mod, mod, strict=strict)

    # Native Deploy (Phase 4.0)
    try:
        from api.native_deploy_routes import router as native_deploy_router

        r["native_deploy"] = native_deploy_router
    except ImportError as e:
        if strict:
            raise ImportError(
                f"[STRICT] Failed to import native_deploy_routes: {e}. "
                f"All route modules must be importable in production."
            ) from e
        print(f"Warning: Could not import native_deploy_routes: {e}")
        r["native_deploy"] = None

    # Model Manager (Phase 6.4.2)
    r["models"] = _try_import_router("api.model_routes", "model_routes", strict=strict)

    # Vision + Design System + Theme (Phase 3.5)
    for name, mod in [
        ("vision", "api.vision_routes"),
        ("design_system", "api.design_system_routes"),
        ("theme", "api.theme_routes"),
    ]:
        r[name] = _try_import_router(mod, mod, strict=strict)

    # V Creator routes
    try:
        from api.wizard_routes import router as wizard_router
        from api.project_routes import router as project_router
        from api.build_routes import router as build_router
        from api.edit_routes import router as edit_router
        from api.checkpoint_routes import router as checkpoint_router
        from api.deploy_routes import router as deploy_router
        from api.collab_routes import router as collab_router
        from api.marketplace_routes import router as vcreator_marketplace_router
        from api.figma_routes import router as figma_router
        from api.database_routes import router as database_router
        from api.auth_setup_routes import router as auth_setup_router
        from api.payment_routes import router as payment_router

        r["vcreator"] = [
            wizard_router,
            project_router,
            build_router,
            edit_router,
            checkpoint_router,
            deploy_router,
            collab_router,
            vcreator_marketplace_router,
            figma_router,
            database_router,
            auth_setup_router,
            payment_router,
        ]
    except ImportError as e:
        if strict:
            raise ImportError(
                f"[STRICT] Failed to import V Creator routes: {e}. "
                f"All route modules must be importable in production."
            ) from e
        print(f"Warning: Could not import V Creator routes: {e}")
        r["vcreator"] = None

    # Developer Ecosystem (Phase M)
    try:
        from api.developer_routes import router as developer_router
        from api.app_submission_routes import router as app_submission_router
        from api.developer_analytics_routes import router as developer_analytics_router

        r["developer_ecosystem"] = [
            developer_router,
            app_submission_router,
            developer_analytics_router,
        ]
    except ImportError as e:
        if strict:
            raise ImportError(
                f"[STRICT] Failed to import developer ecosystem routes: {e}. "
                f"All route modules must be importable in production."
            ) from e
        print(f"Warning: Could not import developer ecosystem routes: {e}")
        r["developer_ecosystem"] = None

    # App Platform (Phase L)
    try:
        from api.app_api_routes import router as app_api_router
        from api.app_auth_routes import router as app_auth_router

        r["app_platform"] = [app_api_router, app_auth_router]
    except ImportError as e:
        if strict:
            raise ImportError(
                f"[STRICT] Failed to import app platform routes: {e}. "
                f"All route modules must be importable in production."
            ) from e
        print(f"Warning: Could not import app platform routes: {e}")
        r["app_platform"] = None

    return r


def mount_routers(app, routers: dict):
    """Mount all discovered routers on the FastAPI app."""
    # Core routers
    _mount = [
        ("v_core", "/api/v-core", ["V-Core"]),
        ("chat", "/api/chat", ["Chat"]),
        ("codegen", "/api/codegen", ["Code Generation"]),
        ("agents", "/api/agents", ["Agents"]),
        ("settings", "/api/settings", ["Settings"]),
        ("voice", "/api", ["Voice"]),
        ("metrics", "/api/metrics", ["Metrics"]),
        ("memory", "/api/memory", ["Memory"]),
    ]
    for name, prefix, tags in _mount:
        if routers.get(name):
            app.include_router(routers[name], prefix=prefix, tags=tags)

    # Routers with built-in prefix (mount at root)
    for name, tags in [
        ("billing", ["Billing"]),
        ("team", ["Teams"]),
        ("template", ["Templates"]),
        ("bmad", ["BMAD"]),
    ]:
        if routers.get(name):
            app.include_router(routers[name], tags=tags)

    # Routers needing /api prefix
    for name, tags in [
        ("analytics", ["Analytics"]),
        ("github", ["GitHub"]),
        ("inbox", ["Inbox"]),
        ("plugin", ["Plugins"]),
        ("clerk", ["Webhooks"]),
        # Day 11: Previously orphaned routes
        ("aiva", ["AIVA"]),
        ("proactive", ["Proactive"]),
        ("prompt", ["Prompts"]),
        ("version_control", ["Version Control"]),
        # Day 12 (REV-2.2): Audited orphan routers
        ("blueprints", ["Blueprints"]),
        ("comments", ["Comments"]),
        ("expert", ["Expert"]),
        # Sprint 14.1 (Gap 1): EU AI Act Article 73 surface
        ("compliance", ["Compliance"]),
        # Phase 26 (Gap G3): /api/v1/transparency/{root,proof}
        ("transparency", ["Transparency"]),
    ]:
        if routers.get(name):
            app.include_router(routers[name], prefix="/api", tags=tags)

    # gateway_routes carries its own /api/gateway/v1 prefix — mount at root
    if routers.get("gateway"):
        app.include_router(routers["gateway"], tags=["Gateway"])

    # Special-prefix routers
    if routers.get("terminal"):
        app.include_router(
            routers["terminal"], prefix="/api/terminal", tags=["Terminal"]
        )
    if routers.get("vos"):
        app.include_router(routers["vos"], prefix="/api/vos", tags=["VOS"])
    if routers.get("kernel"):
        app.include_router(routers["kernel"], prefix="/api/kernel", tags=["Kernel"])
    if routers.get("files"):
        app.include_router(routers["files"], prefix="/api/v1/files", tags=["Files"])

    # V Creator (prefixes built-in to each router)
    if routers.get("vcreator"):
        for vcr in routers["vcreator"]:
            app.include_router(vcr)

    # Vision + Design System + Theme
    if routers.get("vision"):
        app.include_router(routers["vision"], tags=["Vision"])
    if routers.get("design_system"):
        app.include_router(
            routers["design_system"], prefix="/api/v1", tags=["Design System"]
        )
    if routers.get("theme"):
        app.include_router(routers["theme"], prefix="/api/v1", tags=["Themes"])

    # v21.3.1 — VBus efficiency-stats swarm router (mounts /api/v1/swarm/health).
    # The router itself carries no prefix; mounting under /api gives the
    # canonical path test_round_b01-b15 expects.
    try:
        from api.analytics_routes import swarm_router as _swarm_router

        app.include_router(_swarm_router, prefix="/api", tags=["Swarm"])
    except ImportError:
        pass

    # Model Manager (Phase 6.4.2) — prefix built into router
    if routers.get("models"):
        app.include_router(routers["models"], tags=["Models"])

    # Native Deploy
    if routers.get("native_deploy"):
        app.include_router(routers["native_deploy"], tags=["Native Deploy"])

    # App Platform (Phase L)
    if routers.get("app_platform"):
        # App auth + rate limit middleware
        try:
            from middleware.app_auth import app_auth_middleware

            app.middleware("http")(app_auth_middleware)
        except ImportError:
            pass
        try:
            from middleware.app_rate_limit import app_rate_limit_middleware

            app.middleware("http")(app_rate_limit_middleware)
        except ImportError:
            pass
        for apr in routers["app_platform"]:
            app.include_router(apr, tags=["Apps"])

    # Developer Ecosystem (Phase M)
    if routers.get("developer_ecosystem"):
        tags_list = ["Developers", "App Submissions", "Developer Analytics"]
        for idx, dev_r in enumerate(routers["developer_ecosystem"]):
            app.include_router(dev_r, tags=[tags_list[idx]])
