# Independent program-scoped validation — 2026-09-18

31 preserved result/log pairs reviewed. Raw-byte SHA-256 hashes and exact FAIL lines are in validator-all-runs.json. No VM, build or production edits.

| Run | Zero exits / exits | Full order | Stored pass | FAIL with zero exit |
|---|---:|---|---|---:|
| e12-a-1 | 56/57 | True | False | 0 |
| e12-a-2 | 56/57 | True | False | 0 |
| e12-a-3 | 56/57 | True | False | 0 |
| e12-a-4 | 56/57 | True | False | 0 |
| e12-a-5 | 56/57 | True | False | 0 |
| e12-ac-a-1 | 57/57 | True | True | 0 |
| e12-ac-a-2 | 57/57 | True | True | 0 |
| e12-ac-a-3 | 57/57 | True | True | 0 |
| e12-ac-a-4 | 57/57 | True | True | 0 |
| e12-ac-a-5 | 57/57 | True | True | 0 |
| e12-ac-a-6 | 57/57 | True | True | 0 |
| e12-ac-b-1 | 57/57 | True | True | 0 |
| e12-ac-b-2 | 57/57 | True | True | 0 |
| e12-ac-b-3 | 56/57 | True | False | 0 |
| e12-ac-b-4 | 57/57 | True | True | 0 |
| e12-ac-b-5 | 57/57 | True | True | 0 |
| e12-b-1 | 56/57 | True | False | 0 |
| e12-b-2 | 56/57 | True | False | 0 |
| e12-b-3 | 56/57 | True | False | 0 |
| e12-b-4 | 56/57 | True | False | 0 |
| e12-b-5 | 56/57 | True | False | 0 |
| e3-a-1 | 49/50 | False | False | 0 |
| e4-control-1 | 56/57 | True | False | 0 |
| e4-control-2 | 56/57 | True | False | 0 |
| e4-control-3 | 56/57 | True | False | 0 |
| e4-control-4 | 56/57 | True | False | 0 |
| e4-control-5 | 57/57 | True | True | 0 |
| e4-yield-1 | 57/57 | True | True | 0 |
| e4-yield-2 | 57/57 | True | True | 0 |
| e4-yield-3 | 57/57 | True | True | 0 |
| e4-yield-4 | 57/57 | True | True | 0 |

E3 is an incomplete prerequisite failure, not a measured SPSC failure. All complete suites preserve the expected alternating program order. No program-scoped [FAIL] followed by zero exit was found in these logs. These observations do not certify assertions absent from program output.

Ten completed AC-endpoint runs (A1–A6, B1/B2/B4/B5) passed 57/57. A6 has a display-timeout settings change and must remain separately identified. B has only four stable-AC observations, not five; pair7 never started and no new AC UEFI series exists. B3 crossed AC to battery and failed health; it is not a pure AC observation. E4 control5 crossed battery to AC and passed; it is not a pure battery observation. Power labels are coordinator-reported host-evidence interpretations, not inferred from exit status. No release closure is implied.
