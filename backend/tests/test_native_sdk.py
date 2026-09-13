"""
Phase 4.0 QA — Native SDK Generation
=====================================
Tests:
  1. VOS3_SDK.h content validation
  2. Makefile.native content validation
  3. SDK syscall numbers match kernel defines
"""

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.build_routes import VOS3_SDK_H, MAKEFILE_NATIVE

# ═════════════════════════════════════════════════════════════════════════
# 1. VOS3_SDK.h Content
# ═════════════════════════════════════════════════════════════════════════


class TestSDKHeader:
    def test_header_guard(self):
        assert "#ifndef VOS3_SDK_H" in VOS3_SDK_H
        assert "#define VOS3_SDK_H" in VOS3_SDK_H
        assert "#endif" in VOS3_SDK_H

    def test_syscall_numbers(self):
        """Verify key syscall numbers match Linux x86_64 ABI."""
        assert "VOS3_SYS_READ       0" in VOS3_SDK_H
        assert "VOS3_SYS_WRITE      1" in VOS3_SDK_H
        assert "VOS3_SYS_EXIT       60" in VOS3_SDK_H
        assert "VOS3_SYS_MMAP        9" in VOS3_SDK_H
        assert "VOS3_SYS_BRK        12" in VOS3_SDK_H
        assert "VOS3_SYS_OPENAT   257" in VOS3_SDK_H
        assert "VOS3_SYS_GETRANDOM 318" in VOS3_SDK_H

    def test_syscall_functions(self):
        assert "vos3_syscall1" in VOS3_SDK_H
        assert "vos3_syscall2" in VOS3_SDK_H
        assert "vos3_syscall3" in VOS3_SDK_H

    def test_convenience_wrappers(self):
        assert "vos3_exit" in VOS3_SDK_H
        assert "vos3_write" in VOS3_SDK_H
        assert "vos3_read" in VOS3_SDK_H
        assert "vos3_puts" in VOS3_SDK_H

    def test_includes(self):
        assert "#include <stdint.h>" in VOS3_SDK_H
        assert "#include <stddef.h>" in VOS3_SDK_H

    def test_inline_asm(self):
        """SDK should contain x86_64 syscall inline assembly."""
        assert "syscall" in VOS3_SDK_H
        assert "__asm__" in VOS3_SDK_H


# ═════════════════════════════════════════════════════════════════════════
# 2. Makefile.native Content
# ═════════════════════════════════════════════════════════════════════════


class TestMakefile:
    def test_cross_compiler(self):
        assert "x86_64-linux-musl-gcc" in MAKEFILE_NATIVE

    def test_static_flag(self):
        assert "-static" in MAKEFILE_NATIVE

    def test_target(self):
        assert "app.elf" in MAKEFILE_NATIVE

    def test_clean_target(self):
        assert "clean:" in MAKEFILE_NATIVE

    def test_phony(self):
        assert ".PHONY" in MAKEFILE_NATIVE
