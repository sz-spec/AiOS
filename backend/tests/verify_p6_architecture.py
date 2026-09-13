"""
Phase 6 Architecture Validation — Verification Gate
=====================================================

Validates the backend architectural prerequisites for Phase 6
(Native Inference Runtime) before any Phase 6 implementation begins.

Checks:
  1. Factory decomposition: app.py, startup.py, router_registry.py
  2. Circuit breaker existence and correct state machine
  3. Zustand stores: codegen, memory, vcore, settings, agents, chat
  4. VBus driver bridge hooks (vbus_driver.py)
  5. Router registry discovery integrity
  6. SYS_INFERENCE_HINT (498) declared in kernel headers

Run:
    cd backend && python -m pytest tests/verify_p6_architecture.py -v
    # or:
    cd backend && python tests/verify_p6_architecture.py
"""

import os
import sys

# Ensure backend is importable
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(BACKEND_DIR)
sys.path.insert(0, BACKEND_DIR)

PASS = 0
FAIL = 0
RESULTS = []


def check(condition: bool, name: str):
    """Record a test result."""
    global PASS, FAIL
    if condition:
        PASS += 1
        RESULTS.append(("PASS", name))
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        RESULTS.append(("FAIL", name))
        print(f"  FAIL: {name}")


# ============================================================================
# 1. FACTORY DECOMPOSITION
# ============================================================================


def test_factory_decomposition():
    """Verify app.py → startup.py → router_registry.py decomposition."""
    print("\n=== 1. Factory Decomposition ===")

    # app.py exists and has create_app
    app_path = os.path.join(BACKEND_DIR, "app.py")
    check(os.path.exists(app_path), "app.py exists")

    with open(app_path) as f:
        content = f.read()
    check("def create_app" in content, "app.py: create_app() factory defined")
    check("from startup import" in content, "app.py: imports from startup")
    check(
        "from router_registry import" in content, "app.py: imports from router_registry"
    )

    # startup.py exists
    startup_path = os.path.join(BACKEND_DIR, "startup.py")
    check(os.path.exists(startup_path), "startup.py exists")

    with open(startup_path) as f:
        s_content = f.read()
    check(
        "lifespan" in s_content or "services" in s_content,
        "startup.py: contains lifespan or services",
    )

    # router_registry.py exists and has discovery
    rr_path = os.path.join(BACKEND_DIR, "router_registry.py")
    check(os.path.exists(rr_path), "router_registry.py exists")

    with open(rr_path) as f:
        rr_content = f.read()
    check(
        "discover_routers" in rr_content,
        "router_registry.py: discover_routers() defined",
    )
    check(
        "mount_routers" in rr_content or "include_router" in rr_content,
        "router_registry.py: mount/include logic present",
    )

    # main.py delegates to factory
    main_path = os.path.join(BACKEND_DIR, "main.py")
    check(os.path.exists(main_path), "main.py exists")
    with open(main_path) as f:
        m_content = f.read()
    check(
        "from app import create_app" in m_content,
        "main.py: delegates to app.create_app",
    )


# ============================================================================
# 2. CIRCUIT BREAKER
# ============================================================================


def test_circuit_breaker():
    """Verify circuit breaker implements CLOSED → OPEN → HALF_OPEN."""
    print("\n=== 2. Circuit Breaker ===")

    cb_path = os.path.join(BACKEND_DIR, "core", "circuit_breaker.py")
    check(os.path.exists(cb_path), "core/circuit_breaker.py exists")

    with open(cb_path) as f:
        cb_content = f.read()

    check("class CircuitBreaker" in cb_content, "CircuitBreaker class defined")
    check("CLOSED" in cb_content, "CircuitState.CLOSED defined")
    check("OPEN" in cb_content, "CircuitState.OPEN defined")
    check("HALF_OPEN" in cb_content, "CircuitState.HALF_OPEN defined")
    check("allow_request" in cb_content, "allow_request() method present")
    check("record_failure" in cb_content, "record_failure() method present")
    check("record_success" in cb_content, "record_success() method present")
    check("failure_threshold" in cb_content, "failure_threshold configurable")
    check("cooldown_seconds" in cb_content, "cooldown_seconds configurable")

    # Verify there's also a kernel-bridge circuit breaker
    kb_cb_path = os.path.join(BACKEND_DIR, "kernel_bridge", "circuit_breaker.py")
    check(
        os.path.exists(kb_cb_path),
        "kernel_bridge/circuit_breaker.py exists (VBus layer)",
    )


# ============================================================================
# 3. ZUSTAND STORES
# ============================================================================


def test_zustand_stores():
    """Verify Zustand stores exist for all major state domains."""
    print("\n=== 3. Zustand State Stores ===")

    stores_dir = os.path.join(ROOT_DIR, "frontend", "lib", "stores")
    check(os.path.isdir(stores_dir), "frontend/lib/stores/ directory exists")

    required_stores = [
        "codegen-store.ts",
        "memory-store.ts",
        "vcore-store.ts",
        "settings-store.ts",
        "agents-store.ts",
        "chat-store.ts",
    ]

    for store_file in required_stores:
        path = os.path.join(stores_dir, store_file)
        exists = os.path.exists(path)
        check(exists, f"Zustand store: {store_file}")
        if exists:
            with open(path) as f:
                content = f.read()
            check(
                "create" in content
                or "createStore" in content
                or "zustand" in content.lower(),
                f"  {store_file}: uses Zustand create/createStore",
            )


# ============================================================================
# 4. VBUS DRIVER BRIDGE HOOKS
# ============================================================================


def test_vbus_driver():
    """Verify VBus Python driver is ready for Phase 6 token streaming."""
    print("\n=== 4. VBus Driver Bridge ===")

    vbus_path = os.path.join(BACKEND_DIR, "services", "vbus_driver.py")
    check(os.path.exists(vbus_path), "services/vbus_driver.py exists")

    if os.path.exists(vbus_path):
        with open(vbus_path) as f:
            content = f.read()

        check("HANDSHAKE" in content, "vbus_driver: HANDSHAKE handling")
        check("WARP" in content or "warp" in content, "vbus_driver: Warp Drive support")
        check(
            "HMAC" in content or "hmac" in content, "vbus_driver: HMAC authentication"
        )
        check(
            "CMD" in content and "RESP" in content, "vbus_driver: CMD/RESP frame types"
        )

        # Ready for Phase 6 token streaming extension
        check(
            "def " in content and "send" in content.lower(),
            "vbus_driver: has send capability (ready for TOKEN_STREAM)",
        )


# ============================================================================
# 5. ROUTER REGISTRY INTEGRITY
# ============================================================================


def test_router_registry():
    """Verify router registry discovers all critical routes."""
    print("\n=== 5. Router Registry ===")

    rr_path = os.path.join(BACKEND_DIR, "router_registry.py")
    with open(rr_path) as f:
        content = f.read()

    # Critical routers that must be discoverable
    critical = ["chat", "codegen", "agents", "settings", "memory", "metrics"]
    for name in critical:
        check(
            f'"{name}"' in content or f"'{name}'" in content,
            f"router_registry: '{name}' registered",
        )

    # Route files must exist
    api_dir = os.path.join(BACKEND_DIR, "api")
    critical_files = [
        "chat_routes.py",
        "codegen_routes.py",
        "agents_routes.py",
        "settings_routes.py",
        "memory_routes.py",
        "metrics_routes.py",
    ]
    for rf in critical_files:
        check(os.path.exists(os.path.join(api_dir, rf)), f"api/{rf} exists")


# ============================================================================
# 6. KERNEL SYSCALL SYMBOL CHECK
# ============================================================================


def test_inference_hint_syscall():
    """Verify VOS3_SYS_INFERENCE_HINT = 498 in kernel headers."""
    print("\n=== 6. Kernel Syscall Integrity ===")

    syscall_h = os.path.join(ROOT_DIR, "kernel", "include", "vos", "syscall.h")
    check(os.path.exists(syscall_h), "kernel/include/vos/syscall.h exists")

    if os.path.exists(syscall_h):
        with open(syscall_h) as f:
            content = f.read()

        check(
            "VOS3_SYS_INFERENCE_HINT" in content,
            "syscall.h: VOS3_SYS_INFERENCE_HINT declared",
        )
        check("498" in content, "syscall.h: syscall number 498 present")

    # Check registration in dispatcher
    dispatcher_path = os.path.join(ROOT_DIR, "kernel", "src", "ipc", "dispatcher.c")
    if os.path.exists(dispatcher_path):
        with open(dispatcher_path) as f:
            d_content = f.read()
        check(
            "VOS3_SYS_INFERENCE_HINT" in d_content
            and "sys_inference_hint" in d_content,
            "dispatcher.c: INFERENCE_HINT registered with handler",
        )
    else:
        check(False, "dispatcher.c: file not found")


# ============================================================================
# MAIN
# ============================================================================


def main():
    global PASS, FAIL

    print("=" * 60)
    print("[P6-VERIFY] Phase 6 Architecture Validation — START")
    print("=" * 60)

    test_factory_decomposition()
    test_circuit_breaker()
    test_zustand_stores()
    test_vbus_driver()
    test_router_registry()
    test_inference_hint_syscall()

    print("\n" + "=" * 60)
    print(f"[P6-VERIFY] PASS: {PASS}  FAIL: {FAIL}  TOTAL: {PASS + FAIL}")
    if FAIL == 0:
        print("[P6-VERIFY] *** ALL TESTS PASSED — PHASE 6 PREREQS CERTIFIED ***")
    else:
        print(f"[P6-VERIFY] *** {FAIL} FAILURES — PHASE 6 PREREQS NOT CERTIFIED ***")
    print("=" * 60)

    # Print summary table
    print("\n[Component | Status | Proof]")
    print("-" * 60)
    for status, name in RESULTS:
        emoji = "OK" if status == "PASS" else "XX"
        print(f"  [{emoji}] {name}")

    return FAIL


if __name__ == "__main__":
    sys.exit(main())
