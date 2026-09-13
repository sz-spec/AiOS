# VOS

The canonical repository for consolidating the VOS projects into one AI operating system.

This repository is being assembled. It does not yet contain the integrated runtime.
Existing repositories in `VOS3/`, `VOS3-Cyber/`, `VOS-Cyber-Standard/`,
`vos.v1/`, and `vos/` are local migration inputs and are excluded from this Git
repository. Their histories and uncommitted work remain in place.

The final runtime code will live directly in this repository. Legacy projects
must be reconciled into that code, with their unique capabilities accounted for.

## Consolidation

Run `python3 consolidation/inventory.py` to compare the eleven local source
trees by relative path and SHA-256. The generated `consolidation/inventory.json`
is local and ignored by Git. The scanner excludes dependencies, common build
outputs, environment files, key files and symlinks; it is a comparison aid,
not a secret scanner or proof that migration is complete.

The initial inventory found 1,147 paths present in only one source, 5,965 paths
with identical copies, and 2,086 paths with differing contents. Different
contents require review before choosing or merging implementations.

Next steps:

1. Reconcile backend, frontend, kernel and desktop implementations and their tests.
2. Establish one dependency configuration and startup flow for the integrated system.
3. Verify agent execution, identity, isolation, persistence and user-facing flows together.
4. Retire legacy copies only after their code and capabilities have been accounted for.

No remote repository or deployment is configured by this initialization.
