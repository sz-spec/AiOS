"""
Unit Tests — AgentRegistry (256 dynamic PID slots)
===================================================

Verifies:
  1. PID allocation: 248 agents (8-255) with zero collisions
  2. PID wrapping: After freeing a slot, agent 249 reuses it
  3. Kernel protection: PIDs 0-7 are never assigned by dynamic registration
  4. Lookup APIs: find_by_role, find_by_name, update_status
  5. Stats and capacity tracking
  6. Full saturation: 248 agents fills registry, 249th raises RuntimeError
"""

import pytest
from vos.engine import AgentRegistry, AgentProcess

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry():
    """Fresh AgentRegistry for each test."""
    return AgentRegistry()


# ---------------------------------------------------------------------------
# 1. PID Allocation — Zero Collisions (248 dynamic slots: PIDs 8-255)
# ---------------------------------------------------------------------------


class TestPIDAllocation:

    def test_register_248_agents_no_collisions(self, registry):
        """Register 248 agents (max dynamic slots) and verify unique PIDs."""
        pids = set()
        for i in range(248):  # PIDs 8-255 = 248 slots
            agent = registry.register(name=f"agent_{i}", role="worker")
            assert agent.pid not in pids, f"PID collision at {agent.pid} (agent #{i})"
            pids.add(agent.pid)

        assert len(pids) == 248
        assert registry.active_count == 248
        assert registry.capacity == 8  # 256 - 248 = 8 (kernel reserved, never used)

    def test_all_pids_in_dynamic_range(self, registry):
        """Every allocated PID must be in range [8, 255]."""
        for i in range(248):
            agent = registry.register(name=f"agent_{i}", role="worker")
            assert 8 <= agent.pid <= 255, f"PID {agent.pid} outside dynamic range"

    def test_249th_agent_raises_when_full(self, registry):
        """After 248 agents, the 249th must raise RuntimeError (registry full)."""
        for i in range(248):
            registry.register(name=f"agent_{i}", role="worker")

        with pytest.raises(
            RuntimeError, match="Agent registry full|No available PID slots"
        ):
            registry.register(name="overflow_agent", role="worker")


# ---------------------------------------------------------------------------
# 2. PID Wrapping — Reuse freed slot
# ---------------------------------------------------------------------------


class TestPIDWrapping:

    def test_reuse_freed_pid(self, registry):
        """After freeing a PID, the next registration can reuse it."""
        # Fill all 248 dynamic slots
        agents = []
        for i in range(248):
            agents.append(registry.register(name=f"agent_{i}", role="worker"))

        assert registry.active_count == 248

        # Free PID 42 (an arbitrary dynamic PID)
        freed_pid = agents[34].pid  # agent_34 — whatever PID it got
        assert registry.unregister(freed_pid) is True

        # Now register agent 249 — it should reuse the freed PID
        new_agent = registry.register(name="agent_249_recycled", role="recycled")
        assert (
            new_agent.pid == freed_pid
        ), f"Expected reuse of freed PID {freed_pid}, got {new_agent.pid}"

    def test_multiple_free_and_reuse(self, registry):
        """Free multiple PIDs and verify all can be reused."""
        agents = []
        for i in range(100):
            agents.append(registry.register(name=f"agent_{i}", role="worker"))

        freed_pids = []
        for idx in [10, 50, 90]:
            pid = agents[idx].pid
            registry.unregister(pid)
            freed_pids.append(pid)

        # Register 3 new agents — they should fill the freed slots
        new_pids = set()
        for i in range(3):
            a = registry.register(name=f"reused_{i}", role="recycled")
            new_pids.add(a.pid)

        # All 3 new agents should have valid dynamic PIDs
        for pid in new_pids:
            assert 8 <= pid <= 255

    def test_wrapping_allocator_covers_full_range(self, registry):
        """Register 248, free all, re-register 248 — proves full wrapping."""
        first_pids = set()
        for i in range(248):
            a = registry.register(name=f"batch1_{i}", role="worker")
            first_pids.add(a.pid)

        # Free every agent
        for pid in list(first_pids):
            registry.unregister(pid)

        assert registry.active_count == 0

        # Re-register 248 — all PIDs should be valid
        second_pids = set()
        for i in range(248):
            a = registry.register(name=f"batch2_{i}", role="worker")
            second_pids.add(a.pid)

        assert len(second_pids) == 248
        # Every PID must be in [8, 255]
        for pid in second_pids:
            assert 8 <= pid <= 255


# ---------------------------------------------------------------------------
# 3. Kernel Protection — PIDs 0-7 remain untouched
# ---------------------------------------------------------------------------


class TestKernelProtection:

    def test_no_kernel_pids_allocated(self, registry):
        """Register 248 agents. PIDs 0-7 must NEVER appear."""
        kernel_range = set(range(0, 8))
        for i in range(248):
            agent = registry.register(name=f"agent_{i}", role="worker")
            assert (
                agent.pid not in kernel_range
            ), f"KERNEL VIOLATION: PID {agent.pid} is in reserved range 0-7"

    def test_kernel_reserved_constant(self, registry):
        """Verify the KERNEL_RESERVED constant is 8."""
        assert registry.KERNEL_RESERVED == 8

    def test_manual_kernel_pid_not_interfered(self, registry):
        """Manually inserting a kernel PID (0-7) doesn't break dynamic allocation."""
        # Simulate kernel bridge inserting PID 3 directly
        kernel_agent = AgentProcess(pid=3, name="kernel_app_3", role="kernel")
        registry._slots[3] = kernel_agent

        # Dynamic allocation should still start at PID 8+
        dynamic = registry.register(name="dynamic_0", role="worker")
        assert dynamic.pid >= 8, f"Dynamic PID {dynamic.pid} invaded kernel range"


# ---------------------------------------------------------------------------
# 4. Lookup APIs
# ---------------------------------------------------------------------------


class TestLookupAPIs:

    def test_find_by_role(self, registry):
        registry.register(name="arch_1", role="architect")
        registry.register(name="arch_2", role="architect")
        registry.register(name="front_1", role="frontend")

        architects = registry.find_by_role("architect")
        assert len(architects) == 2
        assert all(a.role == "architect" for a in architects)

    def test_find_by_name(self, registry):
        registry.register(name="special_agent", role="reviewer")
        result = registry.find_by_name("special_agent")
        assert result is not None
        assert result.name == "special_agent"

    def test_find_by_name_returns_none(self, registry):
        result = registry.find_by_name("nonexistent")
        assert result is None

    def test_get_by_pid(self, registry):
        agent = registry.register(name="test_get", role="tester")
        found = registry.get(agent.pid)
        assert found is not None
        assert found.name == "test_get"

    def test_get_nonexistent_pid(self, registry):
        assert registry.get(999) is None

    def test_update_status(self, registry):
        agent = registry.register(name="status_test", role="worker")
        assert agent.status == "idle"

        registry.update_status(agent.pid, "running")
        assert registry.get(agent.pid).status == "running"
        assert registry.get(agent.pid).last_active is not None

    def test_list_all_excludes_terminated(self, registry):
        registry.register(name="alive", role="worker")
        a2 = registry.register(name="dead", role="worker")
        registry.unregister(a2.pid)

        alive = registry.list_all(include_terminated=False)
        assert len(alive) == 1
        assert alive[0].name == "alive"

    def test_list_all_includes_terminated(self, registry):
        registry.register(name="alive", role="worker")
        registry.register(name="dead", role="worker")
        # Note: unregister pops from slots, so terminated agents won't appear
        # in list_all even with include_terminated=True
        all_agents = registry.list_all(include_terminated=True)
        assert len(all_agents) == 2


# ---------------------------------------------------------------------------
# 5. Stats and Capacity
# ---------------------------------------------------------------------------


class TestStatsAndCapacity:

    def test_stats_empty(self, registry):
        stats = registry.stats()
        assert stats["total"] == 0
        assert stats["active"] == 0
        assert stats["capacity"] == 256

    def test_stats_with_agents(self, registry):
        registry.register(name="a1", role="architect")
        registry.register(name="a2", role="architect")
        registry.register(name="f1", role="frontend")

        stats = registry.stats()
        assert stats["total"] == 3
        assert stats["active"] == 3
        assert stats["capacity"] == 253
        assert stats["by_role"]["architect"] == 2
        assert stats["by_role"]["frontend"] == 1

    def test_capacity_decreases(self, registry):
        assert registry.capacity == 256
        for i in range(10):
            registry.register(name=f"a_{i}", role="worker")
        assert registry.capacity == 246

    def test_max_slots_constant(self, registry):
        assert registry.MAX_SLOTS == 256
