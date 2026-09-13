# `infra/k8s/sandboxes/` — vOS Sprint 15 / Item C8

Kubernetes Sandbox CRD templates for deploying vOS agent runtimes with
the SIG-Apps **Agent Sandbox** primitive (March 2026,
https://kubernetes.io/blog/2026/03/20/running-agents-on-kubernetes-with-agent-sandbox/).

## Files

| File | Purpose |
|------|---------|
| `sandbox-crd.yaml` | The Sandbox CRD definition. Apply once per cluster. Defines the `Sandbox` resource type. |
| `vos-agent-sandbox.yaml` | Sample Sandbox manifest for a vOS agent. Uses `runtime: gvisor-magi` and references the policy in `backend/sandbox/gvisor_config.json`. |
| `vos-agent-deployment.yaml` | Sample Deployment that references the Sandbox above via `spec.template.spec.sandbox`. |
| `rbac.yaml` | ServiceAccount + Role + RoleBinding scoped to the Sandbox controller. |
| `gvisor-runtime-class.yaml` | RuntimeClass for the gVisor runsc binary so kubelet picks it. |

## Apply order

```bash
# Cluster-wide one-time setup:
kubectl apply -f gvisor-runtime-class.yaml
kubectl apply -f sandbox-crd.yaml

# Namespace setup:
kubectl apply -f rbac.yaml -n vos-agents

# Per-deployment:
kubectl apply -f vos-agent-sandbox.yaml -n vos-agents
kubectl apply -f vos-agent-deployment.yaml -n vos-agents
```

## Verify the sandbox is active

```bash
kubectl get sandbox -n vos-agents
# Expect: STATUS=Ready, RUNTIME=gvisor-magi

kubectl describe pod -l app=vos-agent -n vos-agents
# Expect: Annotations: io.kubernetes.cri.untrusted-workload: "true"
#         RuntimeClassName: gvisor
```

## Honest scope ceiling

The SIG-Apps Sandbox CRD spec is still draft as of May 2026 (final
expected late Q3 2026). The YAML in this directory tracks the
**March-2026 documented spec**; any breaking spec changes between now
and final release will require a one-time YAML update. Field changes
will be tracked in `docs/compliance/ISO_27090_DRAFT.md` under "K8s
Sandbox API drift" (a watchlist item).

## Cross-references

- Policy file consumed by the sandbox runtime: `backend/sandbox/gvisor_config.json`
- Loader + schema validator: `backend/sandbox/gvisor_loader.py`
- Tests: `backend/tests/security/test_gvisor_magi_loader.py`
