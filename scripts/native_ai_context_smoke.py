#!/usr/bin/env python3
"""Diagnostic AI-context lifetime plus continued normal user-space boot."""
import re
import native_boot_smoke as boot
BASE_CLASSIFY = boot.classify_serial
MARKER = '[AI-CTX-LIFETIME] PASS held=1 detached=1 duplicate=1 replacement=1 irq_deferred=1 finalized=2'

def classify_serial(raw, exit_status, requested_cpus=1):
    result = BASE_CLASSIFY(raw, exit_status, requested_cpus)
    clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', raw)
    records = [line.strip() for line in clean.splitlines() if '[AI-CTX-LIFETIME]' in line]
    if len(records) != 1 or not records[0].endswith(MARKER):
        result['failures'].append('missing, malformed or duplicate AI context marker')
    elif ('The AI-Native Operating System' not in clean or
          clean.index(MARKER) > clean.index('The AI-Native Operating System')):
        result['failures'].append('missing subsequent normal user-space progress')
    if '[FAIL]' in clean or 'AI-CTX-LIFETIME: ' in clean:
        result['failures'].append('AI context failure diagnostic')
    result['diagnostic_scope'] = 'controlled kernel lifetime interleaving; not an SMP race proof'
    result['passed'] = result['passed'] and not result['failures']
    return result

if __name__ == '__main__':
    boot.classify_serial = classify_serial
    raise SystemExit(boot.main())
