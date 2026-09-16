# Portable creator-exit evidence

See the [qualification report](../native-shm-exit.md) for ownership contracts,
source identity, acceptance tests and remaining limits.

Each matrix directory retains build/tool output, aggregate result and four raw
serial/result pairs. Serial logs use `serial.txt`; build logs use `build.txt`.
`source-manifest.json` hashes archived inputs. `security-independent.json`
reclassifies the archived raw logs and recomputes actual artifact/source hashes.
Host output is preserved in `observer-tests.txt` and `c-tests.txt`; source and
redacted evidence scans have separate JSON reports and hash-verification review.
`manifest.json` hashes every other file, excluding itself.

Original execution paths retain the temporary directory suffix `20260915`; this
packet was finalized on 2026-09-17. The source commit predates qualification.
Paths and captured timestamps are preserved, not rewritten. Large source archives
and ISOs remain local; hashes bind their identities. Observation timeout is an
intentional smoke-test endpoint and passes only with all required evidence.

Registry retirement alone is not PMM reclamation proof. Kernel backing controls
supply separate physical-reference/page-count evidence. These QEMU runs do not
qualify all hardware, arbitrary concurrent shared-VM mutation or remote kill safety.
