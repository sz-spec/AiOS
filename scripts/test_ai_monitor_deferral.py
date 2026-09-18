#!/usr/bin/env python3
"""Keep heavyweight AI monitor work off the timer interrupt stack."""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest

if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function


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

    def test_actual_drain_preserves_coalesced_deadlines(self) -> None:
        scheduler = (ROOT / "kernel/src/sched/scheduler.c").read_text()
        monitor = (ROOT / "kernel/src/mm/ai_monitor.c").read_text()
        drain = function(scheduler, "void vos3_sched_process_deferred(")
        instruction = '__asm__ volatile ("pushfq; popq %0" : "=r"(rflags));'
        self.assertEqual(drain.count(instruction), 1)
        drain = drain.replace(instruction, "rflags = mock_flags;")
        monitor_tick = function(monitor, "void vos3_ai_monitor_tick(")
        code = r'''
#include <assert.h>
#include <stdint.h>
static uint64_t mock_flags=512, now, observed_tick;
static uint32_t cpu, g_deferred_active[256], g_deferred_pending[256];
static int g_reap_pending, g_ai_guard_dirty, g_tcp_work_pending;
static volatile int g_ai_monitor_pending;
static int g_ai_monitor_initialized;
static struct { uint64_t tick_count, last_report_tick, total_accesses,
 total_reads, total_writes, total_faults, anomaly_count; } g_monitor_stats;
static struct { uint32_t telemetry_interval; } g_config;
static unsigned calls[5], reports, phase, publish_inside, reenter_inside;
static uint32_t get_cpu_id(void){return cpu;}
static uint64_t vos3_timer_get_ticks(void){return now;}
#define VOS3_INFO(...) ((void)++reports)
void vos3_sched_process_deferred(void);
void vos3_sched_request_deferred(void);
static void vos3_shm_reap_creators(void){}
static void vos3_vmm_reap_address_spaces(void){}
static void vos3_ai_guard_reap_contexts(void){}
static void vos3_task_reap(void){}
static void vos3_ai_guard_reprotect_tick(void){}
static void vos3_tcp_timer_tick(void){}
static void observe(unsigned n, uint64_t tick){
 assert(mock_flags&512); assert(g_deferred_active[cpu]);
 assert(n==phase); phase=(phase+1)%5; calls[n]++;
 assert(tick==now); observed_tick=tick;
}
static void vos3_ai_guard_integrity_tick(uint64_t t){
 observe(0,t);
 if(publish_inside){
  publish_inside=0;
  __atomic_store_n(&g_ai_monitor_pending,1,__ATOMIC_RELEASE);
  vos3_sched_request_deferred();
 }
 if(reenter_inside){
  reenter_inside=0;
  unsigned before=calls[0]; vos3_sched_process_deferred();
  assert(calls[0]==before); /* no recursive monitor traversal */
 }
}
static void vos3_ai_guard_pressure_tick(void){observe(1,now);}
static void vos3_ai_timer_tick(uint64_t t){observe(2,t);}
static void vos3_ai_drift_watchdog_tick(uint64_t t){observe(3,t);}
static void vos3_nmi_watchdog_tick(uint64_t t){observe(4,t);}
''' + '\n'.join((
            function(scheduler, "void vos3_sched_request_deferred_cpu("),
            function(scheduler, "void vos3_sched_request_deferred("),
            monitor_tick, drain,
        )) + r'''
static void publish(uint64_t tick){
 now=tick;
 __atomic_store_n(&g_ai_monitor_pending,1,__ATOMIC_RELEASE);
 vos3_sched_request_deferred();
}
static void drain_at(uint64_t tick){publish(tick);vos3_sched_process_deferred();}
int main(void){
 /* Uninitialized monitor must not dispatch any subsystem. */
 drain_at(900);assert(calls[0]==0&&g_monitor_stats.tick_count==0);
 g_ai_monitor_initialized=1;g_config.telemetry_interval=1000;
 g_monitor_stats.total_accesses=1;
 /* IRQ-off must preserve both the general work bit and monitor publication. */
 mock_flags=0;drain_at(998);assert(!calls[0]&&g_ai_monitor_pending&&g_deferred_pending[0]);
 mock_flags=512;vos3_sched_process_deferred();assert(calls[0]==1&&!reports);
 /* Many ticks coalesce; all five callbacks see the absolute tick exactly once. */
 publish(999);publish(1000);publish(1001);vos3_sched_process_deferred();
 assert(calls[0]==2&&reports==1&&g_monitor_stats.last_report_tick==1001);
 for(unsigned i=0;i<5;i++)assert(calls[i]==2);
 drain_at(1001);drain_at(1000);assert(calls[0]==2&&reports==1);
 drain_at(1999);assert(reports==1);
 drain_at(2001);assert(reports==2);
 /* Never loop through missed periods, even across a very large gap. */
 drain_at(9001);assert(reports==3&&g_monitor_stats.last_report_tick==9001);
 g_monitor_stats.total_accesses=0;
 drain_at(10001);assert(reports==3&&g_monitor_stats.last_report_tick==10001);
 g_monitor_stats.anomaly_count=1;
 drain_at(11001);assert(reports==4);
 g_config.telemetry_interval=0;
 drain_at(12001);assert(reports==4&&g_monitor_stats.last_report_tick==11001);
 /* An interrupt publication during a callback survives recursive safe points. */
 publish_inside=reenter_inside=1;
 drain_at(12002);assert(g_ai_monitor_pending&&g_deferred_pending[0]&&!g_deferred_active[0]);
 now=12003;vos3_sched_process_deferred();
 assert(observed_tick==12003&&!g_ai_monitor_pending&&!g_deferred_pending[0]);
#ifdef NATIVE_SMP_TEST
 /* Only BSP consumes global monitor work. AP reaping must leave it pending. */
 cpu=3;publish(12004);vos3_sched_process_deferred();
 assert(g_ai_monitor_pending&&observed_tick==12003);
 cpu=0;vos3_sched_request_deferred();vos3_sched_process_deferred();
 assert(!g_ai_monitor_pending&&observed_tick==12004);
#endif
 /* Upper clock boundary: no addition overflow and no repeated reporting. */
 g_config.telemetry_interval=10;
 drain_at(UINT64_MAX-1);unsigned before=reports;
 drain_at(UINT64_MAX);assert(reports==before);
 before=calls[0];drain_at(UINT64_MAX);assert(calls[0]==before);
 assert(phase==0);
 for(unsigned i=1;i<5;i++)assert(calls[i]==calls[0]);
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="vos-monitor-deferred-") as directory:
            path = Path(directory)
            (path / "test.c").write_text(code)
            for native in (False, True):
                with self.subTest(native_smp=native):
                    flags = ["-DNATIVE_SMP_TEST"] if native else []
                    build = subprocess.run(
                        ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                         "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                         *flags, str(path / "test.c"), "-o", str(path / "test")],
                        capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(build.returncode, 0, build.stderr)
                    run = subprocess.run([str(path / "test")], capture_output=True,
                                         text=True, timeout=10)
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
