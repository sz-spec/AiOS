# Security council opinion

Reviewer `/root/mcp_upgrade`, source `32187957757ab57d7820d0a63fa62c409003b126`, 2026-09-15. This is one agent's six role reviews, not six independent reviewers or an exhaustive repository audit.

The prioritized release findings are: (1) unqualified concurrent shared-VM revocation, (2) absent universal physical-hardware evidence, (3) non-atomic positional backing I/O, (4) incomplete native credential/capability enforcement, (5) AI policy inheritance explicitly unsupported, (6) conditional AI integrity coverage, (7) hosted offline authentication tests do not certify live providers, (8) unsigned or mutable update inputs need a signed release policy, and (9) VFS authorization requires a dedicated caller-to-inode audit.

Source locations, validation and unreviewed boundaries appear in [kernel isolation](expert-council-2026-09-15/08-kernel-isolation-security.md), [AI policy](expert-council-2026-09-15/09-ai-policy-capabilities.md), [VFS/IPC](expert-council-2026-09-15/10-vfs-ipc-security.md), [network and hosted services](expert-council-2026-09-15/11-network-hosted-boundaries.md), [supply chain](expert-council-2026-09-15/12-supply-chain-updates.md), and [release threat model](expert-council-2026-09-15/13-threat-model-release.md).

Passing bounded native gates supports continued engineering. It does not justify a universal security or release-readiness claim. The final lifetime matrix receives its own artifact-bound assessment in `native-vm-lifetime-security.md`; no pending result is assumed here.
