;; SPDX-License-Identifier: MIT
;; SPDX-FileCopyrightText: 2026 VOS3 Project
;;
;; backend/sandbox/wasm/poc/hello.wat — minimal WASI hello fixture.
;;
;; A 12-instruction module exporting `_start` that does nothing more
;; than return cleanly. Used by test_wasm_sandbox.py to verify the
;; engine + policy plumbing works end-to-end.
;;
;; Compile to .wasm with: wasmtime compile hello.wat
(module
  (func (export "_start")
    nop
    return)
)
