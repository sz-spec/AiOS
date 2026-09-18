#!/usr/bin/env python3
"""Keep heavyweight AI monitor work off the timer interrupt stack."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*\([^;]*?\)\s*\{{", source, re.S)
    if match is None:
        raise AssertionError(f"function {name} not found")
    depth = 1
    cursor = match.end()
    while cursor < len(source) and depth:
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
        cursor += 1
    if depth:
        raise AssertionError(f"unterminated function {name}")
    return source[match.end(): cursor - 1]


class AiMonitorDeferralTest(unittest.TestCase):
    def test_timer_irq_only_enters_scheduler(self) -> None:
        timer = (ROOT / "kernel/src/drivers/timer.c").read_text()
        irq = function_body(timer, "vos3_timer_irq_handler")
        self.assertNotIn("vos3_ai_monitor_tick", irq)
        self.assertIn("vos3_sched_tick", irq)

    def test_scheduler_publishes_then_consumes_monitor_work(self) -> None:
        scheduler = (ROOT / "kernel/src/sched/scheduler.c").read_text()
        tick = function_body(scheduler, "vos3_sched_tick")
        deferred = function_body(scheduler, "vos3_sched_process_deferred")
        self.assertRegex(
            tick,
            r"__atomic_store_n\s*\(\s*&g_ai_monitor_pending\s*,\s*1\s*,"
            r"\s*__ATOMIC_RELEASE\s*\)",
        )
        self.assertRegex(
            deferred,
            r"__atomic_exchange_n\s*\(\s*&g_ai_monitor_pending\s*,\s*0\s*,"
            r"\s*__ATOMIC_ACQ_REL\s*\)\s*!=\s*0\s*\)\s*\{\s*"
            r"vos3_ai_monitor_tick\s*\(\s*\)",
        )
        self.assertIn("rflags & (1ULL << 9)", deferred)

    def test_monitor_uses_absolute_timer_time(self) -> None:
        monitor = (ROOT / "kernel/src/mm/ai_monitor.c").read_text()
        body = function_body(monitor, "vos3_ai_monitor_tick")
        self.assertIn("vos3_timer_get_ticks()", body)
        self.assertNotRegex(body, r"g_monitor_stats\.tick_count\s*\+\+")


if __name__ == "__main__":
    unittest.main()
