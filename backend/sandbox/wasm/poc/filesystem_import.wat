;; SPDX-License-Identifier: MIT
;; SPDX-FileCopyrightText: 2026 VOS3 Project
;;
;; backend/sandbox/wasm/poc/filesystem_import.wat — denied-capability
;; fixture. Imports a fake symbol from "wasi:filesystem". The Phase-1
;; runner must refuse to instantiate (RunResult.failed_imports populated).
(module
  (import "wasi:filesystem" "open" (func $open (param i32) (result i32)))
  (func (export "_start")
    i32.const 0
    call $open
    drop))
