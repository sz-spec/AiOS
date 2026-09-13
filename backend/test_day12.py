#!/usr/bin/env python3
"""
Day 12 Validation: AI Agent <-> Kernel Integration
====================================================

Standalone script that verifies the kernel agent can write and read files
on the VOS3 kernel disk through the serial bridge.

Prerequisites:
  - QEMU running with bridge socket at /tmp/vos3_bridge.sock
  - At least one LLM API key set (OPENAI_API_KEY or ANTHROPIC_API_KEY)

Usage:
  cd backend
  python test_day12.py
"""

import sys
import os

# Ensure backend/ is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env (API keys) — normally done by main.py/FastAPI lifespan
from dotenv import load_dotenv

_backend_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_backend_dir, ".env"))


def main():
    print("=" * 60)
    print("Day 12 Validation: AI Agent <-> Kernel Integration")
    print("=" * 60)

    # --- Step 0: Import and instantiate router ---
    print("\n[1/5] Importing MultiAgentRouter...")
    from ai.agents.router_agent import MultiAgentRouter, AgentType, AgentConfig

    config = AgentConfig(complexity=5, role="coding", enable_memory=True)
    router = MultiAgentRouter(
        config=config,
        agents=[AgentType.KERNEL, AgentType.GENERAL],
    )
    print(f"       Router created with agents: {[a.value for a in router.agents]}")

    # --- Step 1: Write query ---
    write_query = (
        "My secret project codename is APOLLO. "
        "Please save this to the kernel disk as secret.txt"
    )
    print(f"\n[2/5] Sending WRITE query: {write_query[:60]}...")
    result1 = router.run(write_query, thread_id="day12-test")
    answer1 = result1.get("answer", "")
    agent1 = result1.get("selected_agent", "unknown")
    print(f"       Routed to: {agent1}")
    print(f"       Answer: {answer1[:200]}")

    # --- Step 2: Read query ---
    read_query = "What is my secret project codename? Please check the kernel disk."
    print(f"\n[3/5] Sending READ query: {read_query[:60]}...")
    result2 = router.run(read_query, thread_id="day12-test")
    answer2 = result2.get("answer", "")
    agent2 = result2.get("selected_agent", "unknown")
    print(f"       Routed to: {agent2}")
    print(f"       Answer: {answer2[:200]}")

    # --- Step 3: Verify ---
    print("\n[4/5] Checking routing...")
    route_ok = "kernel" in agent1 and "kernel" in agent2
    if route_ok:
        print("       PASS: Both queries routed to kernel_agent")
    else:
        print(f"       WARN: Expected kernel_agent, got {agent1} / {agent2}")

    print("\n[5/5] Checking APOLLO in response...")
    apollo_ok = "APOLLO" in answer2
    if apollo_ok:
        print("       PASS: 'APOLLO' found in read response")
    else:
        print("       FAIL: 'APOLLO' not found in response")
        print(f"       Full response: {answer2}")

    # --- Summary ---
    print("\n" + "=" * 60)
    if route_ok and apollo_ok:
        print("RESULT: ALL CHECKS PASSED")
    else:
        print("RESULT: SOME CHECKS FAILED")
        if not route_ok:
            print("  - Routing check failed")
        if not apollo_ok:
            print("  - APOLLO retrieval check failed")
    print("=" * 60)

    return 0 if (route_ok and apollo_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
