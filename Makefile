# Canonical native build entry. Kernel/user outputs belong to BUILD_DIR.
BUILD_DIR ?= build/native-unified
PRODUCTION ?= 1
HEADLESS_AUDIT ?= 0

.PHONY: native native-bootloader native-build-check consolidation-check help
help:
	@echo 'make native             Build the standalone BIOS/UEFI ISO'
	@echo 'make native-bootloader  Rebuild vendored Limine explicitly'
	@echo 'make native-build-check Test user build dependency/option handling'
	@echo 'make dependencies-check Verify generated profiles and Python/Node locks'
	@echo 'make python-lock        Refresh compatible Python locks from registries'
	@echo 'make hosted-build-check Build/type-check installed Node components'
	@echo 'make consolidation-check Validate all 11-source disposition ledgers'

native:
	@for tool in x86_64-elf-gcc x86_64-elf-ld python3 xorriso mformat; do \
	 command -v "$$tool" >/dev/null || { echo "Missing native build tool: $$tool"; exit 1; }; done
	@bash infra/build_limine.sh
	@$(MAKE) -C kernel BUILD_DIR="$(BUILD_DIR)" PRODUCTION="$(PRODUCTION)" HEADLESS_AUDIT="$(HEADLESS_AUDIT)" iso

native-bootloader:
	@LIMINE_FORCE_REBUILD=1 bash infra/build_limine.sh

native-build-check:
	@python3 scripts/test_native_build_config.py
	@python3 scripts/test_build_invariants.py
	@python3 scripts/test_iso_publication.py
	@python3 scripts/test_ai_oom_host.py
	@python3 scripts/test_ai_kv_zeroing.py
	@python3 scripts/test_ai_monitor_deferral.py
	@python3 scripts/test_deferred_syscall.py
	@python3 scripts/test_deferred_empty_path.py
	@python3 scripts/test_ai_context_lifetime.py
	@python3 scripts/test_ai_context_external_callers.py

consolidation-check:
	@python3 consolidation/reconcile_sources.py
	@cd consolidation && python3 -m unittest test_import_baseline.py test_reconcile_sources.py test_native_dispositions.py test_source_dispositions.py

.PHONY: python-requirements python-lock dependencies-check
python-requirements:
	@python3 scripts/sync_python_requirements.py

python-lock: python-requirements
	@python3 scripts/lock_python_dependencies.py

dependencies-check:
	@python3 scripts/lock_python_dependencies.py --check
	@python3 scripts/check_node_locks.py

.PHONY: hosted-build-check
hosted-build-check: dependencies-check
	@npm --prefix frontend run type-check
	@npm --prefix backend/mcp-server run build
	@npm --prefix sdk/typescript run build
	@npm --prefix backend/code_review run build
