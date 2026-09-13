# Python dependency advisory triage

The installed refreshed macOS environment was audited with pip-audit on
2026-09-14: 9 reported entries, 6 unique advisories across 3 packages after
removing duplicate IDs. None provides a fixed version in this scan. The scan
is not clean; latest-version selection does not resolve these findings.
See `python-dependency-advisories.json` for exact IDs and installed versions.

* ChromaDB 1.1.1: four unique advisories concern remote model code execution
  and tenant authorization in the Chroma HTTP server. Reviewed project code
  uses embedded PersistentClient in core/database/vector_setup.py,
  memory/dev_memory.py and ai/rag/llamaindex_agentic.py, plus a legacy embedded
  Client in semantic_cache.py; no Chroma HTTP server
  deployment or trust_remote_code=True call was found in the reviewed Python
  application paths. This narrows the observed exposure; it does not prove
  unreachable exploitation in every deployment. Do not expose a separate
  Chroma API or delegate tenant isolation to its vulnerable authorization.
* DiskCache 5.6.3: pickle deserialization requires trusting the cache directory.
  It is installed through Ragas; no direct DiskCache import was found in
  project Python code. An attacker-writable cache remains unsafe. This review
  does not prove ownership/permissions for every transitive cache location.
* Ragas 0.4.3: the advisory concerns multimodal faithfulness file/URL processing.
  ai/rag/benchmarks.py selects a fixed dictionary of text metrics; its callers
  cannot choose arbitrary imported metric implementations. No use of the
  vulnerable multimodal collections path was found in that adapter. Ragas
  remains present for evaluation capability, and arbitrary external use of
  the package remains outside this narrowed review.

Existing HuggingFace config scanning flags trust_remote_code and executable
model loader metadata; its tests are recorded separately. It is not a blanket
patch for Chroma's HTTP service or Ragas. No advisory was suppressed, and no
unfixed dependency is being represented as certified safe.

Primary advisory references: [Chroma remote model loading](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c),
[Chroma collection update](https://github.com/advisories/GHSA-36p7-vc44-83pf),
[Chroma scoped RBAC](https://github.com/advisories/GHSA-xph7-9rjv-w5fr),
[Chroma tenant authorization](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr),
[DiskCache](https://github.com/advisories/GHSA-w8v5-vhqr-4h9v),
[Ragas](https://github.com/advisories/GHSA-95ww-475f-pr4f).
