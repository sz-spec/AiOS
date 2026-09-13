# Independent Python advisory exposure review — 2026-09-14

Reviewed the root-authored triage and installed `.venv-upgrade` implementation. The scan remains **9 reported entries / 6 unique advisories / 3 affected packages**; no fixed versions are listed. No audit exclusion, dependency removal, exploit execution, or production source modification was made by this review.

## ChromaDB 1.1.1

The narrowing to embedded deployment is supported for the inspected repository configuration, with one precision correction: `backend/ai/cache/semantic_cache.py:180` also uses a legacy embedded `chromadb.Client(Settings(chroma_db_impl="duckdb+parquet", ...))`, rather than `PersistentClient`. Its compatibility/degraded fallback is a separate concern; it is not evidence of a running Chroma HTTP service.

The actual remote input path reviewed is `/api/memory` -> authenticated route -> `get_dev_memory()` -> fixed `DevMemory()` constructor -> fixed collection and default `all-MiniLM-L6-v2` model. Request bodies supply document text, metadata, query text, limits and memory types, **not embedding-function configuration, model repository, tenant/database configuration or `trust_remote_code`**. `DevMemory` passes collection description metadata; ordinary documents are added after embedding. The LlamaIndex adapter similarly creates a fixed `rag_collection` and selects fixed embedding implementations. The repository adapter creates project-named collections with fixed cosine metadata. Persistence paths originate in trusted construction/environment, not the reviewed memory HTTP schema.

No Chroma HTTP server launch/container/service declaration or Chroma `HttpClient` was found in the searched Python and deployment paths. Therefore the specific collection-create/update model-code execution and HTTP authorization advisory entrypoints were not demonstrated reachable through these inspected application paths. This is a bounded call-path conclusion, not a claim that the vulnerable installed package is safe. A separate exposed Chroma service, untrusted persisted database/configuration, a plugin supplying embedding functions, or a different embedding adapter changes the conclusion.

Primary references: [collection creation code injection](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c), [collection update code injection](https://github.com/advisories/GHSA-36p7-vc44-83pf), [scoped RBAC failure](https://github.com/advisories/GHSA-xph7-9rjv-w5fr), [tenant authorization failure](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr).

### Separate application isolation finding — open

**High-impact multi-user exposure is present independently of the Chroma HTTP advisories.** `backend/api/memory_routes.py` authenticates `user` but uses the same global `get_dev_memory()` store for all principals. Store/query/recent/delete paths do not apply an owner namespace; the router is registered at `/api/memory` by `backend/router_registry.py`. In particular query/recent can expose other principals' records, and delete takes a caller-selected insight ID. This follows the inspected code path; no live cross-user exploit was performed.

The ownership helper is a decorator/registry, not automatic middleware: its own scope comment says existing memory routes still need migration. Embedded Chroma does not repair this application-level isolation gap. Before multi-user exposure, use server-derived per-principal/project namespaces across **all** writes, reads, searches, duplicate detection, aggregates, edits and deletions, and fail closed for legacy records with no verified owner. Test two independent principals against actual route execution. A one-route filter or accepting `ownerId` in request metadata would not fix the full boundary. This needs a coordinated data/ownership migration; this bounded dependency review does not silently reinterpret legacy shared records.

## Ragas 0.4.3

`backend/ai/rag/benchmarks.py` builds a fixed dictionary of four legacy text metric objects (`faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`), and chooses only keys from that dictionary. Questions, answers and contexts become dataset string columns; metric names cannot import arbitrary classes. The installed `_faithfulness.py` uses text `PydanticPrompt` classes. The vulnerable `metrics/collections/multi_modal_faithfulness/util.py` instead processes context strings as images/URLs/files, including a `requests.get` path; the reviewed adapter does not call that collection metric.

No API route directly constructing this evaluator or dynamically selecting its metric implementation was found. Its in-repository call sites are the benchmark helper and examples. Thus an arbitrary URL-shaped text context does not by itself establish reachability to the identified multimodal utility. Text evaluation can still send content to the configured LLM/provider, and is not a promise of offline/private evaluation. Untrusted model output, prompt injection, evaluation resource limits, or future multimodal use require additional review.

Primary reference: [multimodal context SSRF/file handling](https://github.com/advisories/GHSA-95ww-475f-pr4f).

## DiskCache 5.6.3

The concern is real pickle deserialization of attacker-modified local cache contents. Tracing beyond direct-import absence: installed `ragas/cache.py` creates `diskcache.Cache(cache_dir)` only when `DiskCacheBackend` is explicitly instantiated, with default path `.cache`. Ragas LLM/embedding factory cache parameters default to `None`; inspected `evaluation.py` calls those factories without a cache backend. The project text adapter does not provide one. Imports alone therefore do not instantiate the vulnerable local cache path in the traced evaluator.

This does not verify permissions for every transitive user of DiskCache. If enabled, its directory and ancestors must be controlled by the service account, isolated from uploaded projects, agent-generated files and other tenants; reused attacker-writable cache contents must never be deserialized. Enabling the backend later requires explicit trust/ownership design, not reliance on a package version being latest.

Primary reference: [DiskCache unsafe pickle deserialization](https://github.com/advisories/GHSA-w8v5-vhqr-4h9v).

## Disposition

The advisory triage's limited exposure claims are supported with the embedded-client wording correction above. They do **not** justify a clean security gate or multi-user safety claim. The separately found shared memory authorization gap remains open and was reported to the root agent. No reachable critical advisory-specific execution path was demonstrated that could be responsibly patched with a narrow dependency guard in this review. Source inspection is not exhaustive whole-program reachability proof; Linux container runtime, externally provisioned Chroma services, arbitrary extensions and disk permissions require their own evidence.


Follow-up 2026-09-14: the root authorized a scoped memory HTTP isolation remediation, implemented after this independent review. See `memory-principal-isolation.md` for the server-derived namespace design, real Chroma/JSON evidence, legacy quarantine and remaining internal-global/resource/concurrency limitations. This follow-up does not resolve or suppress the six upstream package advisories.
