#
# VOS3 User-Space Makefile
#
# Build targets:
#   make          - Build all user programs
#   make clean    - Clean build artifacts
#   make install  - Install to ramfs image
#

# ============================================================================
# CONFIGURATION
# ============================================================================

# Directories
LIB_DIR     := lib
INC_DIR     := include
SRC_DIR     := src
BUILD_DIR   ?= build
BIN_DIR     := $(BUILD_DIR)/bin

# Cross-compiler (must match kernel)
CROSS := x86_64-elf-

# Tools
CC      := $(CROSS)gcc
AS      := $(CROSS)as
LD      := $(CROSS)ld
AR      := $(CROSS)ar
OBJCOPY := $(CROSS)objcopy
SIZE    := $(CROSS)size

# ============================================================================
# COMPILER FLAGS
# ============================================================================

# Warning flags
WARNINGS := -Wall -Wextra -Wpedantic \
            -Wno-unused-parameter

# GCC builtin include path
SOURCE_DATE_EPOCH ?= 1700000000
export SOURCE_DATE_EPOCH

GCC_INC := $(shell $(CC) -print-file-name=include)

# C flags for user space - strict flags for kernel compatibility
CFLAGS := -std=c11 -ffreestanding -fno-stack-protector \
          -fno-pic -fno-pie -mno-red-zone \
          -mno-sse -mno-sse2 -mno-mmx -mno-80387 \
          -nostdinc -nostdlib \
          -isystem $(GCC_INC) \
          -I$(INC_DIR) \
          $(WARNINGS) \
          -O2 -g

# Benchmark suite mode: pass BENCH_MODE=1 to enable headless bench on boot
# Example: make BENCH_MODE=1
# Appended AFTER CFLAGS := so the flag is not overwritten by the := assignment.
ifeq ($(BENCH_MODE),1)
CFLAGS += -DHEADLESS_BENCH_SUITE=1
endif

# Selective test filter: pass TEST_ONLY=xxx to run only tests matching "xxx"
# Example: make BENCH_MODE=1 TEST_ONLY=futex
# Only affects init.c — other programs are still compiled normally.
ifdef TEST_ONLY
CFLAGS += -DVOS3_TEST_ONLY=\"$(TEST_ONLY)\"
endif

# CFLAGS with SSE enabled for FPU stress test (bench_ai_scale)
CFLAGS_SSE := -std=c11 -ffreestanding -fno-stack-protector \
              -fno-pic -fno-pie -mno-red-zone \
              -msse -msse2 \
              -nostdinc -nostdlib \
              -isystem $(GCC_INC) \
              -I$(INC_DIR) \
              $(WARNINGS) \
              -O2 -g
ifeq ($(BENCH_MODE),1)
CFLAGS_SSE += -DHEADLESS_BENCH_SUITE=1
endif
ifdef TEST_ONLY
CFLAGS_SSE += -DVOS3_TEST_ONLY=\"$(TEST_ONLY)\"
endif

# Assembly flags
ASFLAGS := -g

# Linker flags
LDFLAGS := -nostdlib -static -no-pie \
           -z max-page-size=0x1000

# User space starts at 0x400000
USER_BASE := 0x400000

# ============================================================================
# LIBRARY SOURCES
# ============================================================================

LIB_C_SOURCES := \
    $(LIB_DIR)/syscalls.c \
    $(LIB_DIR)/string.c \
    $(LIB_DIR)/stdio.c \
    $(LIB_DIR)/stdlib.c \
    $(LIB_DIR)/signal.c \
    $(LIB_DIR)/time.c \
    $(LIB_DIR)/dns.c

LIB_ASM_SOURCES := \
    $(LIB_DIR)/crt0.S \
    $(LIB_DIR)/restore_rt.S

LIB_OBJECTS := \
    $(patsubst $(LIB_DIR)/%.c,$(BUILD_DIR)/lib/%.o,$(LIB_C_SOURCES)) \
    $(patsubst $(LIB_DIR)/%.S,$(BUILD_DIR)/lib/%.o,$(LIB_ASM_SOURCES))

# ============================================================================
# PROGRAMS
# ============================================================================

include programs.mk

# Generated ELF files
PROGRAM_ELFS := $(patsubst %,$(BIN_DIR)/%,$(PROGRAMS))

# ============================================================================
# BUILD RULES
# ============================================================================

.PHONY: all clean install

all: $(BUILD_DIR)/libc.a $(PROGRAM_ELFS)
	@echo ""
	@echo "User-space build complete:"
	@ls -la $(BIN_DIR)/

# Build static library
$(BUILD_DIR)/libc.a: $(LIB_OBJECTS) $(BUILD_DIR)/build-config.json Makefile
	@echo "  AR      $@"
	@mkdir -p $(dir $@)
	@rm -f "$@.tmp"
	@$(AR) rcs "$@.tmp" $(LIB_OBJECTS)
	@mv "$@.tmp" "$@"

# Compile library C sources
$(BUILD_DIR)/lib/%.o: $(LIB_DIR)/%.c | $(BUILD_DIR)/lib
	@mkdir -p $(dir $@)
	@echo "  CC      $<"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $< -o $@

# Compile library assembly sources
$(BUILD_DIR)/lib/%.o: $(LIB_DIR)/%.S | $(BUILD_DIR)/lib
	@mkdir -p $(dir $@)
	@echo "  AS      $<"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $< -o $@

# Build program: init
$(BIN_DIR)/init: $(SRC_DIR)/init.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/init.c -o $(BUILD_DIR)/init.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/init.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: sh
$(BIN_DIR)/sh: $(SRC_DIR)/sh.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/sh.c -o $(BUILD_DIR)/sh.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/sh.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: cat
$(BIN_DIR)/cat: $(SRC_DIR)/cat.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/cat.c -o $(BUILD_DIR)/cat.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/cat.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: grep
$(BIN_DIR)/grep: $(SRC_DIR)/grep.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/grep.c -o $(BUILD_DIR)/grep.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/grep.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: wc
$(BIN_DIR)/wc: $(SRC_DIR)/wc.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/wc.c -o $(BUILD_DIR)/wc.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/wc.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: head
$(BIN_DIR)/head: $(SRC_DIR)/head.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/head.c -o $(BUILD_DIR)/head.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/head.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: date
$(BIN_DIR)/date: $(SRC_DIR)/date.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/date.c -o $(BUILD_DIR)/date.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/date.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: uptime
$(BIN_DIR)/uptime: $(SRC_DIR)/uptime.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/uptime.c -o $(BUILD_DIR)/uptime.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/uptime.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: uname
$(BIN_DIR)/uname: $(SRC_DIR)/uname.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/uname.c -o $(BUILD_DIR)/uname.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/uname.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: ai_test
$(BIN_DIR)/ai_test: $(SRC_DIR)/ai_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/ai_test.c -o $(BUILD_DIR)/ai_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/ai_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: ai_diag
$(BIN_DIR)/ai_diag: $(SRC_DIR)/ai_diag.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/ai_diag.c -o $(BUILD_DIR)/ai_diag.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/ai_diag.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: ai_stat
$(BIN_DIR)/ai_stat: $(SRC_DIR)/ai_stat.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/ai_stat.c -o $(BUILD_DIR)/ai_stat.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/ai_stat.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: ai_top
$(BIN_DIR)/ai_top: $(SRC_DIR)/ai_top.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/ai_top.c -o $(BUILD_DIR)/ai_top.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/ai_top.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: ai_stress
$(BIN_DIR)/ai_stress: $(SRC_DIR)/ai_stress.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/ai_stress.c -o $(BUILD_DIR)/ai_stress.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/ai_stress.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: ai_exploit
$(BIN_DIR)/ai_exploit: $(SRC_DIR)/ai_exploit.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/ai_exploit.c -o $(BUILD_DIR)/ai_exploit.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/ai_exploit.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: business_sim
$(BIN_DIR)/business_sim: $(SRC_DIR)/business_sim.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/business_sim.c -o $(BUILD_DIR)/business_sim.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/business_sim.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: setup_wizard (Phase 23)
$(BIN_DIR)/setup_wizard: $(SRC_DIR)/setup_wizard.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/setup_wizard.c -o $(BUILD_DIR)/setup_wizard.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/setup_wizard.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: ws_switch (Phase 23)
$(BIN_DIR)/ws_switch: $(SRC_DIR)/ws_switch.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/ws_switch.c -o $(BUILD_DIR)/ws_switch.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/ws_switch.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: vos3_admin (Phase 24)
$(BIN_DIR)/vos3_admin: $(SRC_DIR)/vos3_admin.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/vos3_admin.c -o $(BUILD_DIR)/vos3_admin.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/vos3_admin.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: ai_audit (Alpha 2.0 Audit Suite)
$(BIN_DIR)/ai_audit: $(SRC_DIR)/ai_audit.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/ai_audit.c -o $(BUILD_DIR)/ai_audit.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/ai_audit.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: udp_test (Day 5 - UDP Socket API Test)
$(BIN_DIR)/udp_test: $(SRC_DIR)/udp_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/udp_test.c -o $(BUILD_DIR)/udp_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/udp_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: tcp_test (Day 6 - TCP Socket API Test)
$(BIN_DIR)/tcp_test: $(SRC_DIR)/tcp_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/tcp_test.c -o $(BUILD_DIR)/tcp_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/tcp_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: disk_test (Day 7 - Block Device Test)
$(BIN_DIR)/disk_test: $(SRC_DIR)/disk_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/disk_test.c -o $(BUILD_DIR)/disk_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/disk_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_csw (Benchmark - Context Switch Latency)
$(BIN_DIR)/bench_csw: $(SRC_DIR)/bench_csw.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_csw.c -o $(BUILD_DIR)/bench_csw.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_csw.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_ai_throughput (AI Agent SHM Throughput Benchmark)
$(BIN_DIR)/bench_ai_throughput: $(SRC_DIR)/bench_ai_throughput.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_ai_throughput.c -o $(BUILD_DIR)/bench_ai_throughput.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_ai_throughput.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_csw_1ms (Context Switch Audit - Minimum Quantum)
$(BIN_DIR)/bench_csw_1ms: $(SRC_DIR)/bench_csw_1ms.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_csw_1ms.c -o $(BUILD_DIR)/bench_csw_1ms.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_csw_1ms.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_hugepage_tlb (HugePage TLB Performance Benchmark)
$(BIN_DIR)/bench_hugepage_tlb: $(SRC_DIR)/bench_hugepage_tlb.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_hugepage_tlb.c -o $(BUILD_DIR)/bench_hugepage_tlb.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_hugepage_tlb.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_soak_5min (5-Minute Soak Test - Chaos Level 2)
$(BIN_DIR)/bench_soak_5min: $(SRC_DIR)/bench_soak_5min.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_soak_5min.c -o $(BUILD_DIR)/bench_soak_5min.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_soak_5min.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_ai_scale (Phase 1.3 AI Stress & Scale - SSE enabled)
$(BIN_DIR)/bench_ai_scale: $(SRC_DIR)/bench_ai_scale.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@ (SSE enabled)"
	@$(CC) $(CFLAGS_SSE) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_ai_scale.c -o $(BUILD_DIR)/bench_ai_scale.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_ai_scale.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_npu_direct (Phase 1.5 NPU-Direct Validation Test)
$(BIN_DIR)/test_npu_direct: $(SRC_DIR)/test_npu_direct.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_npu_direct.c -o $(BUILD_DIR)/test_npu_direct.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_npu_direct.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_2026_frontier (Phase 2 - 2026 Frontier AI Compatibility - SSE enabled)
$(BIN_DIR)/bench_2026_frontier: $(SRC_DIR)/bench_2026_frontier.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@ (SSE enabled)"
	@$(CC) $(CFLAGS_SSE) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_2026_frontier.c -o $(BUILD_DIR)/bench_2026_frontier.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_2026_frontier.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_ai_latency (Phase 2 - SPSC Ring Buffer Benchmark)
$(BIN_DIR)/test_ai_latency: $(SRC_DIR)/test_ai_latency.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_ai_latency.c -o $(BUILD_DIR)/test_ai_latency.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_ai_latency.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_agent_chaos (Phase 2 - Chaos & Stability Audit)
$(BIN_DIR)/test_agent_chaos: $(SRC_DIR)/test_agent_chaos.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_agent_chaos.c -o $(BUILD_DIR)/test_agent_chaos.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_agent_chaos.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_advanced_chaos (Advanced Chaos Audit)
$(BIN_DIR)/test_advanced_chaos: $(SRC_DIR)/test_advanced_chaos.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_advanced_chaos.c -o $(BUILD_DIR)/test_advanced_chaos.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_advanced_chaos.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_health_check (Post-Deploy Health Check)
$(BIN_DIR)/test_health_check: $(SRC_DIR)/test_health_check.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_health_check.c -o $(BUILD_DIR)/test_health_check.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_health_check.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_agent_cluster (Phase 3 Multi-Agent Stress Test)
$(BIN_DIR)/test_agent_cluster: $(SRC_DIR)/test_agent_cluster.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_agent_cluster.c -o $(BUILD_DIR)/test_agent_cluster.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_agent_cluster.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_torture (Phase 3.5 Torture Test)
$(BIN_DIR)/test_torture: $(SRC_DIR)/test_torture.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_torture.c -o $(BUILD_DIR)/test_torture.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_torture.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_vmm_soak (5-Minute VMM Memory Leak Soak Test)
$(BIN_DIR)/test_vmm_soak: $(SRC_DIR)/test_vmm_soak.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_vmm_soak.c -o $(BUILD_DIR)/test_vmm_soak.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_vmm_soak.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_ai_exploit_v2 (Adversarial Security Audit)
$(BIN_DIR)/test_ai_exploit_v2: $(SRC_DIR)/test_ai_exploit_v2.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_ai_exploit_v2.c -o $(BUILD_DIR)/test_ai_exploit_v2.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_ai_exploit_v2.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_max_saturation (Total System Saturation - SSE enabled for FPU stress)
$(BIN_DIR)/test_max_saturation: $(SRC_DIR)/test_max_saturation.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@ (SSE enabled)"
	@$(CC) $(CFLAGS_SSE) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_max_saturation.c -o $(BUILD_DIR)/test_max_saturation.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_max_saturation.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_model_load (Model-Load Audit)
$(BIN_DIR)/test_model_load: $(SRC_DIR)/test_model_load.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_model_load.c -o $(BUILD_DIR)/test_model_load.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_model_load.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_isolate (Benchmark - Memory Isolation Red Team)
$(BIN_DIR)/bench_isolate: $(SRC_DIR)/bench_isolate.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_isolate.c -o $(BUILD_DIR)/bench_isolate.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_isolate.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_persist (Benchmark - Persistence Access Speed)
$(BIN_DIR)/bench_persist: $(SRC_DIR)/bench_persist.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_persist.c -o $(BUILD_DIR)/bench_persist.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_persist.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_overhead (Benchmark - Host Resource Overhead)
$(BIN_DIR)/bench_overhead: $(SRC_DIR)/bench_overhead.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_overhead.c -o $(BUILD_DIR)/bench_overhead.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_overhead.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_sigpipe_test (Benchmark - SIGPIPE Validation)
$(BIN_DIR)/bench_sigpipe_test: $(SRC_DIR)/bench_sigpipe_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_sigpipe_test.c -o $(BUILD_DIR)/bench_sigpipe_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_sigpipe_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_bugfix_test (Kernel Bugfix Validation)
$(BIN_DIR)/bench_bugfix_test: $(SRC_DIR)/bench_bugfix_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_bugfix_test.c -o $(BUILD_DIR)/bench_bugfix_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_bugfix_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_phase13_test (Phase 13 Kernel Feature Tests)
$(BIN_DIR)/bench_phase13_test: $(SRC_DIR)/bench_phase13_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_phase13_test.c -o $(BUILD_DIR)/bench_phase13_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_phase13_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_procfs_test (procfs Virtual Filesystem Tests)
$(BIN_DIR)/bench_procfs_test: $(SRC_DIR)/bench_procfs_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_procfs_test.c -o $(BUILD_DIR)/bench_procfs_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_procfs_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_poll_test (poll() Syscall Tests)
$(BIN_DIR)/bench_poll_test: $(SRC_DIR)/bench_poll_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_poll_test.c -o $(BUILD_DIR)/bench_poll_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_poll_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: ps (Process Status)
$(BIN_DIR)/ps: $(SRC_DIR)/ps.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/ps.c -o $(BUILD_DIR)/ps.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/ps.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_fs_test (Phase F - Filesystem Tests)
$(BIN_DIR)/bench_fs_test: $(SRC_DIR)/bench_fs_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_fs_test.c -o $(BUILD_DIR)/bench_fs_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_fs_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_net_test (Phase F - Network Tests)
$(BIN_DIR)/bench_net_test: $(SRC_DIR)/bench_net_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_net_test.c -o $(BUILD_DIR)/bench_net_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_net_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_stress_test (Phase F - Stress Tests)
$(BIN_DIR)/bench_stress_test: $(SRC_DIR)/bench_stress_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_stress_test.c -o $(BUILD_DIR)/bench_stress_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_stress_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_fuzz_test (Phase F - Syscall Fuzzing Tests)
$(BIN_DIR)/bench_fuzz_test: $(SRC_DIR)/bench_fuzz_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_fuzz_test.c -o $(BUILD_DIR)/bench_fuzz_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_fuzz_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_sched_test (Phase N - Scheduler Tests)
$(BIN_DIR)/bench_sched_test: $(SRC_DIR)/bench_sched_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_sched_test.c -o $(BUILD_DIR)/bench_sched_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_sched_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_app_isolation_test (Phase N - App Isolation Tests)
$(BIN_DIR)/test_native_isolation: $(SRC_DIR)/test_native_isolation.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $< -o $(BUILD_DIR)/test_native_isolation.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ $(BUILD_DIR)/lib/crt0.o $(BUILD_DIR)/test_native_isolation.o $(BUILD_DIR)/libc.a

$(BIN_DIR)/test_native_smp: $(SRC_DIR)/test_native_smp.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC/LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $< -o $(BUILD_DIR)/test_native_smp.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ $(BUILD_DIR)/lib/crt0.o $(BUILD_DIR)/test_native_smp.o $(BUILD_DIR)/libc.a

$(BIN_DIR)/test_signal_lifetime: $(SRC_DIR)/test_signal_lifetime.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $< -o $(BUILD_DIR)/test_signal_lifetime.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ $(BUILD_DIR)/lib/crt0.o $(BUILD_DIR)/test_signal_lifetime.o $(BUILD_DIR)/libc.a

$(BIN_DIR)/test_native_memory: $(SRC_DIR)/test_native_memory.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $< -o $(BUILD_DIR)/test_native_memory.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ $(BUILD_DIR)/lib/crt0.o $(BUILD_DIR)/test_native_memory.o $(BUILD_DIR)/libc.a

# Build program: bench_app_isolation_test (Phase N - App Isolation Tests)
$(BIN_DIR)/bench_app_isolation_test: $(SRC_DIR)/bench_app_isolation_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_app_isolation_test.c -o $(BUILD_DIR)/bench_app_isolation_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_app_isolation_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_posix_fs (Task 1.2 - POSIX File Syscall Validation)
$(BIN_DIR)/test_posix_fs: $(SRC_DIR)/test_posix_fs.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_posix_fs.c -o $(BUILD_DIR)/test_posix_fs.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_posix_fs.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_epoll (Task 1.3 - epoll/select/eventfd Validation)
$(BIN_DIR)/test_epoll: $(SRC_DIR)/test_epoll.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_epoll.c -o $(BUILD_DIR)/test_epoll.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_epoll.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: stress_thread (Task 1.4 - Threading Stress & Validation)
$(BIN_DIR)/stress_thread: $(SRC_DIR)/stress_thread.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/stress_thread.c -o $(BUILD_DIR)/stress_thread.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/stress_thread.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_posix_core (Task 1.5 - POSIX Core Syscall Validation)
$(BIN_DIR)/test_posix_core: $(SRC_DIR)/test_posix_core.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_posix_core.c -o $(BUILD_DIR)/test_posix_core.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_posix_core.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_futex (Task 1.5 - Futex/Mutex Validation)
$(BIN_DIR)/test_futex: $(SRC_DIR)/test_futex.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_futex.c -o $(BUILD_DIR)/test_futex.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_futex.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: bench_thread_test (Task 1.4 - Threading/clone/TLS Validation)
$(BIN_DIR)/bench_thread_test: $(SRC_DIR)/bench_thread_test.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/bench_thread_test.c -o $(BUILD_DIR)/bench_thread_test.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/bench_thread_test.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_net_dispatch (Task 1.6 - Socket vtable + Linux ABI Validation)
$(BIN_DIR)/test_net_dispatch: $(SRC_DIR)/test_net_dispatch.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_net_dispatch.c -o $(BUILD_DIR)/test_net_dispatch.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_net_dispatch.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_shm_dispatch (Task 1.7 - AI-Agent IPC Foundation)
$(BIN_DIR)/test_shm_dispatch: $(SRC_DIR)/test_shm_dispatch.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_shm_dispatch.c -o $(BUILD_DIR)/test_shm_dispatch.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_shm_dispatch.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: diag_shm_stress (Task 1.7 - SHM Stress Test & Leak Audit)
$(BIN_DIR)/diag_shm_stress: $(SRC_DIR)/diag_shm_stress.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/diag_shm_stress.c -o $(BUILD_DIR)/diag_shm_stress.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/diag_shm_stress.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: fake_interp (Task 2.1 - ET_DYN interpreter for PT_INTERP test)
# Must be ET_DYN so the kernel can load it at VOS3_INTERP_BASE.
# Uses -shared (not -pie) to produce ET_DYN WITHOUT a PT_INTERP header
# (same technique as building ld-linux.so — interpreters don't have interpreters).
# No libc dependency — all syscalls are inline.
$(BIN_DIR)/fake_interp: $(SRC_DIR)/fake_interp.c | $(BIN_DIR)
	@echo "  CC+LD   $@ (ET_DYN/shared)"
	@$(CC) $(CFLAGS) -fpic -fno-plt -fvisibility=hidden -MMD -MP -MT "$@" -c $(SRC_DIR)/fake_interp.c -o $(BUILD_DIR)/fake_interp.o
	@$(LD) -shared -Bsymbolic -nostdlib -e _start -z max-page-size=0x1000 -o $@ $(BUILD_DIR)/fake_interp.o
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: hello_dyn (Task 2.1 - Binary with PT_INTERP for testing)
# Uses custom linker script to create a PT_INTERP program header.
$(BIN_DIR)/hello_dyn: $(SRC_DIR)/hello_dyn.c $(SRC_DIR)/hello_dyn.ld $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@ (with PT_INTERP)"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/hello_dyn.c -o $(BUILD_DIR)/hello_dyn.o
	@$(LD) -nostdlib -static -z max-page-size=0x1000 \
		-T $(SRC_DIR)/hello_dyn.ld -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/hello_dyn.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_pt_interp (Task 2.1 - PT_INTERP Test Orchestrator)
$(BIN_DIR)/test_pt_interp: $(SRC_DIR)/test_pt_interp.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_pt_interp.c -o $(BUILD_DIR)/test_pt_interp.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_pt_interp.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: auxv_interp (Task 2.1 QA - Deep auxv validation interpreter)
# ET_DYN shared object, same build pattern as fake_interp.
$(BIN_DIR)/auxv_interp: $(SRC_DIR)/auxv_interp.c | $(BIN_DIR)
	@echo "  CC+LD   $@ (ET_DYN/shared)"
	@$(CC) $(CFLAGS) -fpic -fno-plt -fvisibility=hidden -MMD -MP -MT "$@" -c $(SRC_DIR)/auxv_interp.c -o $(BUILD_DIR)/auxv_interp.o
	@$(LD) -shared -Bsymbolic -nostdlib -e _start -z max-page-size=0x1000 -o $@ $(BUILD_DIR)/auxv_interp.o
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: hello_auxv (Task 2.1 QA - Binary with PT_INTERP -> auxv_interp)
$(BIN_DIR)/hello_auxv: $(SRC_DIR)/hello_auxv.c $(SRC_DIR)/hello_auxv.ld $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@ (with PT_INTERP)"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/hello_auxv.c -o $(BUILD_DIR)/hello_auxv.o
	@$(LD) -nostdlib -static -z max-page-size=0x1000 \
		-T $(SRC_DIR)/hello_auxv.ld -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/hello_auxv.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build musl libc.so (full libc + dynamic linker)
MUSL_BUILD := $(BUILD_DIR)/musl/obj
MUSL_LIB := $(BUILD_DIR)/musl/lib
.PHONY: CHECK_MUSL
CHECK_MUSL:
	@$(MAKE) -C musl -f Makefile.vos3 BUILD="../$(MUSL_BUILD)" LIB_DIR="../$(MUSL_LIB)" --no-print-directory

$(BIN_DIR)/ld-musl-x86_64.so.1: CHECK_MUSL | $(BIN_DIR)
	@cmp -s "$(MUSL_LIB)/libc.so" "$@" || cp "$(MUSL_LIB)/libc.so" "$@"

.PHONY: FORCE_MUSL_LINK_CONFIG
FORCE_MUSL_LINK_CONFIG:
$(BUILD_DIR)/musl-link-config.json: $(BIN_DIR)/ld-musl-x86_64.so.1 FORCE_MUSL_LINK_CONFIG
	@python3 ../kernel/tools/build_config.py "$@" VOS_MUSL_LINK_ "$(MUSL_LIB)/libc.so" "$(MUSL_LIB)/crt1.o" "$(MUSL_LIB)/crti.o" "$(MUSL_LIB)/crtn.o"

# Build program: hello_musl (dynamically-linked against musl libc)
$(BIN_DIR)/hello_musl: $(SRC_DIR)/hello_musl.c $(SRC_DIR)/hello_musl.ld $(BIN_DIR)/ld-musl-x86_64.so.1 | $(BIN_DIR)
	@echo "  CC+LD   $@ (dynamic, musl libc)"
	@$(CC) -std=c11 -ffreestanding -fno-stack-protector \
		-mno-red-zone \
		-nostdinc -isystem $(GCC_INC) \
		-Imusl/include -I$(MUSL_BUILD)/include -Imusl/arch/x86_64 -Imusl/arch/generic \
		-O2 -g -MMD -MP -MT "$@" -c $(SRC_DIR)/hello_musl.c -o $(BUILD_DIR)/hello_musl.o
	@$(LD) -nostdlib -z max-page-size=0x1000 \
		-z pack-relative-relocs=none --hash-style=sysv \
		--allow-shlib-undefined \
		-T $(SRC_DIR)/hello_musl.ld \
		-dynamic-linker /bin/ld-musl-x86_64.so.1 \
		$(MUSL_LIB)/crt1.o $(MUSL_LIB)/crti.o \
		$(BUILD_DIR)/hello_musl.o \
		-L$(MUSL_LIB) -lc \
		$(MUSL_LIB)/crtn.o \
		-o $@
	@echo "  SIZE    $@"
	@$(SIZE) $@

# ============================================================================
# Musl-linked test programs (pattern rule)
# ============================================================================

MUSL_TESTS = test_signal_musl test_env_musl test_fileio_musl test_malloc_musl test_fork_musl test_pipe_musl test_stat_musl test_pthread_musl test_stress_mt

MUSL_TEST_BINS = $(patsubst %,$(BIN_DIR)/%,$(MUSL_TESTS))

define MUSL_TEST_RULE
$(BIN_DIR)/$(1): $(SRC_DIR)/$(1).c $(SRC_DIR)/$(1).ld $(BIN_DIR)/ld-musl-x86_64.so.1 | $(BIN_DIR)
	@echo "  CC+LD   $$@ (dynamic, musl libc)"
	@$$(CC) -std=c11 -ffreestanding -fno-stack-protector \
		-mno-red-zone \
		-nostdinc -isystem $$(GCC_INC) \
		-Imusl/include -I$(MUSL_BUILD)/include -Imusl/arch/x86_64 -Imusl/arch/generic \
		-O2 -g -MMD -MP -MT "$$@" -c $(SRC_DIR)/$(1).c -o $$(BUILD_DIR)/$(1).o
	@$$(LD) -nostdlib -z max-page-size=0x1000 \
		-z pack-relative-relocs=none --hash-style=sysv \
		--allow-shlib-undefined \
		-T $(SRC_DIR)/$(1).ld \
		-dynamic-linker /bin/ld-musl-x86_64.so.1 \
		$(MUSL_LIB)/crt1.o $(MUSL_LIB)/crti.o \
		$$(BUILD_DIR)/$(1).o \
		-L$(MUSL_LIB) -lc \
		$(MUSL_LIB)/crtn.o \
		-o $$@
	@echo "  SIZE    $$@"
	@$$(SIZE) $$@
endef

$(foreach t,$(MUSL_TESTS),$(eval $(call MUSL_TEST_RULE,$(t))))
$(MUSL_TEST_BINS) $(BIN_DIR)/hello_musl: $(BUILD_DIR)/musl-link-config.json

# Build program: test_ai_guard (Phase F - AI Guard Enforcement Tests)
$(BIN_DIR)/test_ai_guard: $(SRC_DIR)/test_ai_guard.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_ai_guard.c -o $(BUILD_DIR)/test_ai_guard.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_ai_guard.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_net_e2e (Phase G - Network E2E Test Suite)
$(BIN_DIR)/test_net_e2e: $(SRC_DIR)/test_net_e2e.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_net_e2e.c -o $(BUILD_DIR)/test_net_e2e.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_net_e2e.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_integration (Phase H - VFS + Process Integration Tests)
$(BIN_DIR)/test_integration: $(SRC_DIR)/test_integration.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_integration.c -o $(BUILD_DIR)/test_integration.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_integration.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_sustained (Sustained 60s Stability Audit)
$(BIN_DIR)/test_sustained: $(SRC_DIR)/test_sustained.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_sustained.c -o $(BUILD_DIR)/test_sustained.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_sustained.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_dynlink (Task 2.1 QA - Comprehensive dynlink test suite)
$(BIN_DIR)/test_dynlink: $(SRC_DIR)/test_dynlink.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_dynlink.c -o $(BUILD_DIR)/test_dynlink.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_dynlink.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Build program: test_security_check (Stabilization Sprint Security Validation)
$(BIN_DIR)/test_security_check: $(SRC_DIR)/test_security_check.c $(BUILD_DIR)/libc.a | $(BIN_DIR)
	@echo "  CC+LD   $@"
	@$(CC) $(CFLAGS) -MMD -MP -MT "$@" -c $(SRC_DIR)/test_security_check.c -o $(BUILD_DIR)/test_security_check.o
	@$(LD) $(LDFLAGS) -Ttext=$(USER_BASE) -o $@ \
		$(BUILD_DIR)/lib/crt0.o \
		$(BUILD_DIR)/test_security_check.o \
		$(BUILD_DIR)/libc.a
	@echo "  SIZE    $@"
	@$(SIZE) $@

# Create directories
$(BUILD_DIR) $(BUILD_DIR)/lib $(BIN_DIR):
	@mkdir -p $@

# Clean
clean:
	@echo "Cleaning user-space..."
	@rm -rf $(BUILD_DIR)

# ============================================================================
# RAMFS IMAGE
# ============================================================================

# Create a simple ramfs image with the binaries
RAMFS_IMG := $(BUILD_DIR)/ramfs.img

ramfs: $(PROGRAM_ELFS)
	@echo "Creating ramfs image..."
	@mkdir -p $(BUILD_DIR)/ramfs/bin
	@cp $(BIN_DIR)/* $(BUILD_DIR)/ramfs/bin/
	@echo "Ramfs contents:"
	@ls -laR $(BUILD_DIR)/ramfs/

# ============================================================================
# HELP
# ============================================================================

help:
	@echo "VOS3 User-Space Build System"
	@echo ""
	@echo "Targets:"
	@echo "  all     - Build all programs (default)"
	@echo "  clean   - Remove build artifacts"
	@echo "  ramfs   - Create ramfs image"
	@echo "  help    - Show this help"
	@echo ""
	@echo "Programs: $(PROGRAMS)"

# Option changes and source/header dependencies are tracked in each build tree.
export VOS_USER_CC := $(CC)
export VOS_USER_LD := $(LD)
export VOS_USER_AR := $(AR)
export VOS_USER_LIB_OBJECTS := $(LIB_OBJECTS)
export VOS_USER_CFLAGS := $(CFLAGS)
export VOS_USER_CFLAGS_SSE := $(CFLAGS_SSE)
export VOS_USER_LDFLAGS := $(LDFLAGS)
export VOS_USER_EPOCH := $(SOURCE_DATE_EPOCH)
export VOS_USER_BASE := $(USER_BASE)
export VOS_USER_PROGRAMS := $(PROGRAMS)
export VOS_USER_TOOLCHAIN := $(shell $(CC) --version | head -n 1)
.PHONY: FORCE_USER_CONFIG
FORCE_USER_CONFIG:
$(BUILD_DIR)/build-config.json: FORCE_USER_CONFIG | $(BUILD_DIR)
	@python3 ../kernel/tools/build_config.py "$@" VOS_USER_
$(LIB_OBJECTS) $(PROGRAM_ELFS): $(BUILD_DIR)/build-config.json Makefile programs.mk
$(LIB_OBJECTS) $(PROGRAM_ELFS): $(wildcard include/*.h include/*/*.h)
-include $(wildcard $(BUILD_DIR)/*.d $(BUILD_DIR)/lib/*.d)
