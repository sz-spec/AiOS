# Proposal: New CycloneDX 1.6 Component Type — `agent`

**Authors:** vOS Engineering Team
**Status:** Draft — Sprint 15 / Item P5
**Date:** 2026-05-24
**Target spec:** CycloneDX 1.6 (next minor release after 1.5)
**Tracking issue (to file upstream):** _TBD — open after counsel review_
**License:** CC-BY 4.0 (matching CycloneDX spec license)

---

## 1. Problem statement

CycloneDX 1.5 (https://cyclonedx.org/specification/overview/) defines the following `component.type` values:

  - `application`
  - `framework`
  - `library`
  - `container`
  - `platform`
  - `operating-system`
  - `device`
  - `device-driver`
  - `firmware`
  - `file`
  - `machine-learning-model` (added in 1.5)
  - `data`
  - `cryptographic-asset` (added in 1.6 draft)

**Gap:** there is no first-class component type for an **AI agent** — a deployed software entity that combines a model, a set of tools, a system prompt, an identity, and runtime governance policies into a single addressable artifact.

Today, agent providers ship agents as one of:
  - `application` — loses the agent-specific provenance (model, tools, system prompt)
  - `machine-learning-model` — wrong; agents are not models, they USE models
  - opaque ZIP/.vpk packages with no SBOM type at all

This makes downstream compliance tooling (ISO/IEC 27090 §6 SBOM ingestion, EU AI Act Annex IV technical-documentation export, NIST AI RMF MAP function evidence) unable to distinguish agents from generic applications — and unable to track the cryptographic agent identity (SPIFFE WIT-SVID, IETF AAP token) that is the load-bearing audit primitive.

---

## 2. Proposal

Add `agent` as a first-class `component.type` value in CycloneDX 1.6.

### 2.1 JSON schema fragment

```json
{
  "type": "agent",
  "bom-ref": "agent:vos3-builder-001",
  "name": "vos3-builder-agent",
  "version": "1.4.2",
  "supplier": { "name": "vOS Project" },
  "agent": {
    "identity": {
      "type": "spiffe",
      "uri": "spiffe://vos.dev/ns/agents/sa/vos3-builder-001",
      "trust-domain": "vos.dev",
      "verification-bundle-ref": "vos:bundle:vos.dev-jwks"
    },
    "model-refs": [
      "vos3:model:data/models/claude-sonnet-4-6.gguf",
      "vos3:model:data/models/embed-mpnet.safetensors"
    ],
    "tools": [
      {
        "name": "read_kernel_file",
        "description": "Read a file from the kernel-attached filesystem",
        "permissions": ["fs:read:/kernel/**"],
        "is-remote": false
      },
      {
        "name": "fetch_url",
        "description": "Outbound HTTP GET; egress firewall enforced",
        "permissions": ["net:egress:https"],
        "is-remote": true,
        "egress-allowlist-ref": "VOS3_EGRESS_ALLOWLIST"
      }
    ],
    "system-prompt-ref": {
      "digest": { "alg": "SHA-256", "content": "<hex>" },
      "size-bytes": 8421
    },
    "governance": {
      "policy-refs": ["intent-manifest:v2:<digest>"],
      "audit-store": "/api/compliance/audit/failures",
      "isolation-runtime": "gvisor-magi"
    },
    "autonomy-level": "level-3-bounded",
    "actor-type": "agent"
  },
  "hashes": [
    {
      "alg": "SHA-256",
      "content": "<digest of the canonical JSON of the agent bundle>"
    }
  ]
}
```

### 2.2 Required + optional fields

| Field | Cardinality | Description |
|-------|-------------|-------------|
| `agent.identity` | required | Cryptographic identity (SPIFFE, x.509, IETF AAP token, or other) |
| `agent.identity.type` | required | `spiffe` \| `x509` \| `oidc` \| `iam-role` \| `aap-token` |
| `agent.identity.uri` | required | Canonical identifier URI |
| `agent.identity.trust-domain` | required for SPIFFE | The issuing trust domain |
| `agent.identity.verification-bundle-ref` | optional | Cross-reference to a JWKS / cert chain component |
| `agent.model-refs` | required | List of `bom-ref` strings pointing at the `machine-learning-model` components this agent uses |
| `agent.tools` | required | List of tool descriptors. Empty array is permitted (zero-tool agent). |
| `agent.tools[].name` | required | Tool identifier (matches MCP / OpenAPI tool name) |
| `agent.tools[].permissions` | required | Capability strings (vOS uses `<resource>:<action>:<scope>` format; spec is implementation-defined) |
| `agent.tools[].is-remote` | required | `true` if the tool reaches outside the agent's sandbox |
| `agent.tools[].egress-allowlist-ref` | optional | Pointer to operator-managed egress policy |
| `agent.system-prompt-ref.digest` | required | Hash of the canonical system prompt bytes |
| `agent.system-prompt-ref.size-bytes` | required | For size-attack detection |
| `agent.governance.policy-refs` | optional | Pointers to IntentManifest / OPA policy documents |
| `agent.governance.audit-store` | optional | Endpoint where agent decisions are recorded |
| `agent.governance.isolation-runtime` | optional | `gvisor` \| `gvisor-magi` \| `kata` \| `kata-cc` \| `process` \| `none` |
| `agent.autonomy-level` | optional | Per the AI-SA autonomy taxonomy (`level-0-tool-assisted` ... `level-5-full`); see `docs/AI_SA_AUTONOMY_LEVEL_MAPPING.md` |
| `agent.actor-type` | optional | `agent` \| `agent_chain` \| `human_supervised` |

### 2.3 Backward compatibility

Tools that don't recognize `agent` as a component type SHOULD fall back to treating it as `application` for inventory purposes. The `agent` block is a new top-level child of `components[]`, parallel to `mlbom` (1.5) and `cryptography` (1.6 draft); existing CycloneDX schema validators that allow additional properties pass it through.

---

## 3. Why this, and why now

1. **EU AI Act Annex IV §V.1 demands documented model + tool + governance evidence per AI system.** Annex IV-compliant SBOMs need a structure that separates the model artifact from the agent system that uses it. The 1.5 `machine-learning-model` type covers the model alone; this proposal covers the agent.

2. **ISO/IEC FDIS 27090 §6 (lifecycle integration) explicitly references SBOM** as the evidence pathway for tracking AI workloads. The Standard is silent on the SBOM type; CycloneDX needs to provide the right primitive before the Standard publishes (~Q3 2026) or operators will improvise.

3. **NIST AI RMF MAP function (April-2026 Critical Infrastructure Profile concept note)** asks for a "complete inventory of AI components". Today operators ship `application` types and bolt agent metadata into `properties[]` arrays — fine in principle, but every consumer parses differently. A first-class type makes inventory tooling vendor-neutral.

4. **The CNCF agentic-standards work (March 2026)** standardizes `agentic.k8s.io/*` labels at the Kubernetes layer. The SBOM-layer counterpart (`type: agent`) closes the bottom-half of the chain: the deployed agent's identity in K8s maps 1:1 to its SBOM component.

5. **Prior art:**
   - `machine-learning-model` was added in 1.5 (2024) after a parallel evolution: the AI/ML BOM working group's pre-spec proposals, then a formal addition. This proposal follows the same pattern.
   - `cryptographic-asset` (1.6 draft) demonstrates the spec's openness to new types that serve specific compliance audiences.

---

## 4. Backwards-compatibility risk: low

  - Existing SBOM consumers ignore `agent` and fall through to `application` handling — no breakage.
  - The new `agent` block is additive; consumers that don't parse it lose only the agent-specific fields.
  - The proposed JSON schema fragment uses fields already namespaced under `agent.*`, no top-level field collisions.

---

## 5. Open questions for the CycloneDX working group

1. **Should `agent` extend or coexist with `application`?** vOS proposal: coexist. An agent IS an application, but the documented fields are too agent-specific to belong under `application.*`.

2. **Should `model-refs` be a `componentRef` array (with `bom-ref` resolution) or inline `componentMlBom` snapshots?** vOS proposal: bom-ref-only. Snapshots bloat the BOM; the resolution lookup is cheap.

3. **How does this interact with the proposed `cryptographic-asset` type for the signing key?** The agent's identity verification bundle could itself be a `cryptographic-asset` component, referenced by `bom-ref`. We propose this is the canonical layering.

4. **What's the autonomy-level taxonomy reference?** vOS uses the AI-SA 0-5 scale (see `docs/AI_SA_AUTONOMY_LEVEL_MAPPING.md`); the upstream community may prefer NIST AI 100-1 or another standard. We recommend `autonomy-level` carries a URI rather than an enum to leave the choice open.

5. **MCP / A2A protocol declaration:** should `agent.tools[]` reference the MCP / A2A protocol the tool was registered under? The 2026-07-28 MCP RC includes this metadata; making it BOM-visible would help compliance.

---

## 6. Reference implementation

vOS Sprint 15 ships an in-tree generator that emits `type: machine-learning-model` for the model artifact (Sprint 15 / I1) and is structured to emit `type: agent` as a one-config-flip when the upstream spec lands.

```python
# infra/security/build_sbom.py — when spec passes:
def _component_agent(rel: str, manifest: dict) -> dict:
    return {
        "type": "agent",
        "bom-ref": f"agent:{manifest['agent_id']}",
        "name": manifest["name"],
        "version": manifest["version"],
        "agent": {
            "identity": manifest["spiffe_identity"],
            "model-refs": manifest["model_refs"],
            "tools": manifest["tools"],
            "system-prompt-ref": manifest["system_prompt"],
            "governance": manifest["governance"],
            "autonomy-level": manifest["autonomy_level"],
            "actor-type": manifest["actor_type"],
        },
        "hashes": [{"alg": "SHA-256", "content": manifest["digest"]}],
    }
```

The full reference implementation lives in vOS's `infra/security/build_sbom.py` and will be open-sourced under the same MIT license as the rest of the repo on the same day this proposal is filed upstream.

---

## 7. Open issue / PR plan

1. **File CycloneDX GitHub issue** with this document as the body. Tag `enhancement`, `1.6-target`.
2. **Open WG discussion** at the next OWASP CycloneDX WG meeting (monthly).
3. **Submit JSON-schema PR** to https://github.com/CycloneDX/specification once WG aligns on field names.
4. **Land in CycloneDX 1.6** (target: late Q4 2026 per the WG roadmap).

---

## 8. Honest scope ceiling

  - This is a **proposal**, not a merged spec change. The vOS-internal implementation (Sprint 15 / I1 emits `machine-learning-model` for model artifacts; the agent type is the gap this doc proposes to close) will track the upstream decision and update.
  - The schema fragment above is illustrative; the final CycloneDX 1.6 schema may rename or restructure fields. vOS commits to one-pass refresh when the spec finalizes.
  - vOS's `autonomy-level` URI references the AI-SA taxonomy in `docs/AI_SA_AUTONOMY_LEVEL_MAPPING.md`, which itself is honest-scope-ceiling marked (no upstream AI-SA schema URL exists yet).

---

## Sources

- [CycloneDX Specification overview](https://cyclonedx.org/specification/overview/)
- [CycloneDX AI/ML-BOM capability page](https://cyclonedx.org/capabilities/mlbom/)
- [CNCF Cloud-Native Agentic Standards (March 2026)](https://www.cncf.io/blog/2026/03/23/cloud-native-agentic-standards/)
- [ISO/IEC FDIS 27090](https://www.iso.org/standard/56581.html)
- [NIST AI RMF Critical Infrastructure Profile concept note (April 2026)](https://www.nist.gov/system/files/documents/2026/04/08/Concept%20Note_%20Development%20of%20the%20NIST%20AI%20RMF%20Trustworthy%20Use%20of%20AI%20in%20Critical%20Infrastructure%20Profile.pdf)
- [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
- [IETF draft-ietf-oauth-spiffe-client-auth-01](https://datatracker.ietf.org/doc/draft-ietf-oauth-spiffe-client-auth/) (SPIFFE workload identity binding)
- [IETF draft-ietf-wimse-arch-07](https://datatracker.ietf.org/doc/draft-ietf-wimse-arch/) (workload identity)
- [MCP 2026-07-28 release candidate (locked 2026-05-21)](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/) (protocol declaration)
