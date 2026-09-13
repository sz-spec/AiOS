;; SPDX-License-Identifier: MIT
;; SPDX-FileCopyrightText: 2026 VOS3 Project
;;
;; backend/sandbox/wasm/poc/runaway.wat — fuel-exhaustion fixture.
;;
;; Infinite loop that does nothing but consume instructions. The Phase-1
;; runner with the default Policy (fuel=1_000_000) must trap this with
;; `exceeded_limit="fuel"` in well under 100 ms.
;;
;; Compile to .wasm with: wasmtime compile runaway.wat
(module
  (func (export "_start")
    (loop $forever
      br $forever)))
