# V OS MCP Agent Server - Operations Runbook

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Daily Operations](#daily-operations)
- [Monitoring & Alerts](#monitoring--alerts)
- [Incident Response](#incident-response)
- [Common Issues & Solutions](#common-issues--solutions)
- [Maintenance Procedures](#maintenance-procedures)
- [Scaling](#scaling)
- [Backup & Recovery](#backup--recovery)
- [Security Operations](#security-operations)

---

## Overview

### Service Information

| Property | Value |
|----------|-------|
| Service Name | V OS MCP Agent Server |
| Version | 1.0.1 |
| Team | V OS Platform |
| On-Call | #v-os-oncall (Slack) |
| Escalation | See [Escalation Matrix](#escalation-matrix) |

### SLA Targets

| Metric | Target | Critical Threshold |
|--------|--------|-------------------|
| Availability | 99.9% | < 99.5% |
| P50 Latency | < 100ms | > 200ms |
| P99 Latency | < 500ms | > 1000ms |
| Error Rate | < 0.1% | > 1% |

### Key URLs

| Environment | URL | Health Check |
|-------------|-----|--------------|
| Production | https://mcp.v-os.ai/mcp | https://mcp.v-os.ai/health |
| Staging | https://mcp-staging.v-os.ai/mcp | https://mcp-staging.v-os.ai/health |
| Development | http://localhost:8080/mcp | http://localhost:8080/health |

---

## Architecture

### Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           LOAD BALANCER                                  │
│                        (nginx-ingress / ALB)                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
            ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
            │   Pod 1     │ │   Pod 2     │ │   Pod 3     │
            │  MCP Server │ │  MCP Server │ │  MCP Server │
            └─────────────┘ └─────────────┘ └─────────────┘
                    │               │               │
                    └───────────────┼───────────────┘
                                    ▼
                            ┌─────────────┐
                            │    Redis    │
                            │   (Cache)   │
                            └─────────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
    ┌─────────────┐         ┌─────────────┐         ┌─────────────┐
    │   OpenAI    │         │  Anthropic  │         │    Azure    │
    │     API     │         │     API     │         │   OpenAI    │
    └─────────────┘         └─────────────┘         └─────────────┘
```

### Dependencies

| Dependency | Purpose | Criticality |
|------------|---------|-------------|
| Redis | Distributed caching (L3) | High |
| OpenAI API | LLM provider | High |
| Anthropic API | LLM provider | High |
| Jaeger | Distributed tracing | Medium |
| Prometheus | Metrics collection | Medium |

---

## Daily Operations

### Morning Health Check (9:00 AM)

```bash
#!/bin/bash
# morning-check.sh

echo "=== V OS MCP Server Morning Check ==="

# 1. Check all pods are running
echo "Checking pods..."
kubectl get pods -n v-os -l app.kubernetes.io/name=v-os-mcp

# 2. Check health endpoint
echo "Checking health..."
curl -s https://mcp.v-os.ai/health | jq .

# 3. Check cache stats
echo "Checking cache..."
curl -s -X POST https://mcp.v-os.ai/mcp \
  -H "Content-Type: application/json" \
  -H "x-api-key: $V_OS_API_KEY" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"v_cache_stats","arguments":{}}}' | jq .

# 4. Check error rate (last 1 hour)
echo "Checking error rate..."
curl -s "http://prometheus:9090/api/v1/query?query=rate(vos_errors_total[1h])" | jq .

# 5. Check Redis connection
echo "Checking Redis..."
kubectl exec -n v-os deploy/v-os-redis -- redis-cli ping

echo "=== Morning Check Complete ==="
```

### Key Metrics to Monitor

| Metric | Query | Normal Range |
|--------|-------|--------------|
| Request Rate | `rate(vos_requests_total[5m])` | 10-1000 rps |
| Error Rate | `rate(vos_errors_total[5m]) / rate(vos_requests_total[5m])` | < 0.001 |
| P99 Latency | `histogram_quantile(0.99, vos_request_latency_ms)` | < 500ms |
| Cache Hit Rate | `vos_cache_hit_rate` | > 0.4 |
| Active Sessions | `vos_cache_sessions_total` | varies |
| Memory Usage | `container_memory_usage_bytes` | < 800Mi |
| CPU Usage | `container_cpu_usage_seconds_total` | < 70% |

---

## Monitoring & Alerts

### Alert Definitions

#### Critical Alerts (Page immediately)

```yaml
# High Error Rate
- alert: VosMcpHighErrorRate
  expr: |
    rate(vos_errors_total[5m]) / rate(vos_requests_total[5m]) > 0.01
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "High error rate on V OS MCP Server"
    description: "Error rate is {{ $value | printf \"%.2f\" }}%"
    runbook: "https://runbook.v-os.ai/mcp/high-error-rate"

# Service Down
- alert: VosMcpServiceDown
  expr: up{job="v-os-mcp-server"} == 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "V OS MCP Server is down"
    runbook: "https://runbook.v-os.ai/mcp/service-down"

# High Latency
- alert: VosMcpHighLatency
  expr: |
    histogram_quantile(0.99, rate(vos_request_latency_ms_bucket[5m])) > 1000
  for: 10m
  labels:
    severity: critical
  annotations:
    summary: "High P99 latency on V OS MCP Server"
    description: "P99 latency is {{ $value }}ms"
```

#### Warning Alerts (Slack notification)

```yaml
# Low Cache Hit Rate
- alert: VosMcpLowCacheHitRate
  expr: vos_cache_hit_rate < 0.3
  for: 30m
  labels:
    severity: warning
  annotations:
    summary: "Low cache hit rate"
    description: "Cache hit rate is {{ $value | printf \"%.2f\" }}%"

# High Memory Usage
- alert: VosMcpHighMemory
  expr: |
    container_memory_usage_bytes{container="mcp-server"} / 
    container_spec_memory_limit_bytes > 0.8
  for: 15m
  labels:
    severity: warning
  annotations:
    summary: "High memory usage on MCP server"

# Pod Restarts
- alert: VosMcpPodRestarts
  expr: |
    increase(kube_pod_container_status_restarts_total{
      namespace="v-os", container="mcp-server"
    }[1h]) > 3
  labels:
    severity: warning
  annotations:
    summary: "MCP server pods restarting frequently"
```

### Dashboards

| Dashboard | URL | Purpose |
|-----------|-----|---------|
| Overview | grafana.v-os.ai/d/vos-mcp-overview | High-level metrics |
| Performance | grafana.v-os.ai/d/vos-mcp-perf | Latency & throughput |
| Agents | grafana.v-os.ai/d/vos-mcp-agents | Per-agent metrics |
| Cache | grafana.v-os.ai/d/vos-mcp-cache | Cache performance |
| Errors | grafana.v-os.ai/d/vos-mcp-errors | Error analysis |

---

## Incident Response

### Escalation Matrix

| Severity | Response Time | Escalation Path |
|----------|--------------|-----------------|
| SEV1 (Critical) | 15 min | On-call → Team Lead → CTO |
| SEV2 (Major) | 1 hour | On-call → Team Lead |
| SEV3 (Minor) | 4 hours | On-call |
| SEV4 (Low) | Next business day | Ticket |

### Incident Response Checklist

#### Step 1: Assess (5 min)

```bash
# Quick health check
curl -s https://mcp.v-os.ai/health | jq .

# Check pod status
kubectl get pods -n v-os

# Check recent logs
kubectl logs -n v-os -l app.kubernetes.io/name=v-os-mcp --tail=100

# Check metrics
# Open Grafana dashboard
```

#### Step 2: Communicate (2 min)

```
# Slack message template
@channel 🚨 INCIDENT: V OS MCP Server

Status: Investigating
Impact: [Describe user impact]
Start Time: [Time]
Current Actions: [What you're doing]

Updates every 15 minutes.
```

#### Step 3: Mitigate

See [Common Issues & Solutions](#common-issues--solutions)

#### Step 4: Resolve & Document

```markdown
## Incident Report Template

**Incident ID:** INC-YYYY-MM-DD-XXX
**Duration:** X hours Y minutes
**Severity:** SEVX
**Impact:** X users affected, Y% error rate

### Timeline
- HH:MM - Alert triggered
- HH:MM - On-call acknowledged
- HH:MM - Root cause identified
- HH:MM - Mitigation applied
- HH:MM - Service restored

### Root Cause
[Description]

### Resolution
[What fixed it]

### Action Items
- [ ] Item 1
- [ ] Item 2
```

---

## Common Issues & Solutions

### Issue 1: High Error Rate

**Symptoms:**
- Error rate > 1%
- Users report failures
- Alert: VosMcpHighErrorRate

**Diagnosis:**

```bash
# Check error logs
kubectl logs -n v-os -l app.kubernetes.io/name=v-os-mcp | grep -i error | tail -50

# Check provider status
curl -s https://status.openai.com/api/v2/status.json | jq .
curl -s https://status.anthropic.com/api/v2/status.json | jq .

# Check error breakdown
curl -s "http://prometheus:9090/api/v1/query?query=sum(rate(vos_errors_total[5m])) by (error_type)"
```

**Solutions:**

| Cause | Solution |
|-------|----------|
| Provider outage | Switch to backup provider or enable fallback |
| Rate limiting | Increase rate limits or add backoff |
| Invalid requests | Check client logs, update validation |
| Memory pressure | Scale up or restart pods |

```bash
# Enable fallback to Anthropic if OpenAI is down
kubectl set env deployment/v-os-mcp-server -n v-os FALLBACK_PROVIDER=anthropic

# Restart pods (rolling)
kubectl rollout restart deployment/v-os-mcp-server -n v-os
```

---

### Issue 2: High Latency

**Symptoms:**
- P99 > 1000ms
- Users report slow responses
- Alert: VosMcpHighLatency

**Diagnosis:**

```bash
# Check latency breakdown
curl -s "http://prometheus:9090/api/v1/query?query=histogram_quantile(0.99, rate(vos_request_latency_ms_bucket[5m]))"

# Check cache hit rate
curl -s "http://prometheus:9090/api/v1/query?query=vos_cache_hit_rate"

# Check Redis latency
kubectl exec -n v-os deploy/v-os-redis -- redis-cli --latency

# Check provider latency (from traces)
# Open Jaeger: https://jaeger.v-os.ai
```

**Solutions:**

| Cause | Solution |
|-------|----------|
| Low cache hit rate | Review cache TTL, check cache key strategy |
| Redis slow | Check Redis memory, consider scaling |
| Provider slow | Switch to faster model or provider |
| CPU throttling | Increase CPU limits |

```bash
# Increase cache TTL
kubectl set env deployment/v-os-mcp-server -n v-os CACHE_TTL=600000

# Scale Redis
kubectl scale deployment/v-os-redis -n v-os --replicas=3

# Scale MCP server
kubectl scale deployment/v-os-mcp-server -n v-os --replicas=5
```

---

### Issue 3: Service Unavailable

**Symptoms:**
- Health check failing
- 503 errors
- All pods down

**Diagnosis:**

```bash
# Check pod status
kubectl get pods -n v-os -o wide

# Check events
kubectl get events -n v-os --sort-by='.lastTimestamp' | tail -20

# Check node status
kubectl get nodes

# Check resource quotas
kubectl describe resourcequota -n v-os
```

**Solutions:**

| Cause | Solution |
|-------|----------|
| OOMKilled | Increase memory limits |
| Image pull error | Check image registry, credentials |
| Node failure | Pods will reschedule automatically |
| Resource quota | Increase quota or scale down |

```bash
# Force pod recreation
kubectl delete pods -n v-os -l app.kubernetes.io/name=v-os-mcp

# Check why pods won't start
kubectl describe pod -n v-os <pod-name>

# Increase memory limit
kubectl patch deployment v-os-mcp-server -n v-os -p '{"spec":{"template":{"spec":{"containers":[{"name":"mcp-server","resources":{"limits":{"memory":"2Gi"}}}]}}}}'
```

---

### Issue 4: Redis Connection Failure

**Symptoms:**
- Cache not working
- Connection refused errors
- High latency (no caching)

**Diagnosis:**

```bash
# Check Redis pod
kubectl get pods -n v-os -l app.kubernetes.io/name=redis

# Test Redis connection
kubectl exec -n v-os deploy/v-os-mcp-server -- nc -zv v-os-redis 6379

# Check Redis logs
kubectl logs -n v-os deploy/v-os-redis

# Check Redis memory
kubectl exec -n v-os deploy/v-os-redis -- redis-cli info memory
```

**Solutions:**

```bash
# Restart Redis
kubectl rollout restart deployment/v-os-redis -n v-os

# Clear Redis data (if corrupted)
kubectl exec -n v-os deploy/v-os-redis -- redis-cli FLUSHALL

# Switch to in-memory caching (temporary)
kubectl set env deployment/v-os-mcp-server -n v-os REDIS_URL=""
```

---

### Issue 5: Authentication Failures

**Symptoms:**
- 401 Unauthorized errors
- Users can't connect

**Diagnosis:**

```bash
# Check auth errors
kubectl logs -n v-os -l app.kubernetes.io/name=v-os-mcp | grep -i "401\|auth\|unauthorized"

# Test authentication
curl -v -H "x-api-key: test-key" https://mcp.v-os.ai/health
```

**Solutions:**

| Cause | Solution |
|-------|----------|
| Invalid API key | Verify key in secrets |
| Expired token | Regenerate tokens |
| CORS issues | Check ALLOWED_ORIGINS |

```bash
# Update API key secret
kubectl create secret generic v-os-mcp-secrets -n v-os \
  --from-literal=V_OS_API_KEY=new-key \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart to pick up new secret
kubectl rollout restart deployment/v-os-mcp-server -n v-os
```

---

## Maintenance Procedures

### Deployment

```bash
#!/bin/bash
# deploy.sh <version>

VERSION=$1
NAMESPACE="v-os"

echo "Deploying V OS MCP Server v${VERSION}"

# 1. Update image
kubectl set image deployment/v-os-mcp-server \
  mcp-server=ghcr.io/v-os/mcp-agent-server:${VERSION} \
  -n ${NAMESPACE}

# 2. Wait for rollout
kubectl rollout status deployment/v-os-mcp-server -n ${NAMESPACE}

# 3. Verify health
sleep 10
curl -s https://mcp.v-os.ai/health | jq .

# 4. Check for errors
kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/name=v-os-mcp --tail=50 | grep -i error

echo "Deployment complete"
```

### Rollback

```bash
#!/bin/bash
# rollback.sh

NAMESPACE="v-os"

echo "Rolling back V OS MCP Server"

# Check rollout history
kubectl rollout history deployment/v-os-mcp-server -n ${NAMESPACE}

# Rollback to previous version
kubectl rollout undo deployment/v-os-mcp-server -n ${NAMESPACE}

# Wait for rollout
kubectl rollout status deployment/v-os-mcp-server -n ${NAMESPACE}

# Verify
curl -s https://mcp.v-os.ai/health | jq .

echo "Rollback complete"
```

### Cache Clear

```bash
#!/bin/bash
# clear-cache.sh

echo "Clearing V OS MCP cache"

# Clear Redis
kubectl exec -n v-os deploy/v-os-redis -- redis-cli FLUSHALL

# Restart pods to clear in-memory cache
kubectl rollout restart deployment/v-os-mcp-server -n v-os

echo "Cache cleared"
```

### Secret Rotation

```bash
#!/bin/bash
# rotate-secrets.sh

echo "Rotating secrets"

# 1. Generate new API key
NEW_KEY=$(openssl rand -hex 32)

# 2. Update secret
kubectl create secret generic v-os-mcp-secrets -n v-os \
  --from-literal=V_OS_API_KEY=${NEW_KEY} \
  --from-literal=OPENAI_API_KEY=${OPENAI_API_KEY} \
  --from-literal=ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY} \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Restart pods
kubectl rollout restart deployment/v-os-mcp-server -n v-os

# 4. Update clients with new key
echo "New API key: ${NEW_KEY}"
echo "Update all clients with new key!"
```

---

## Scaling

### Horizontal Scaling (Manual)

```bash
# Scale to 5 replicas
kubectl scale deployment/v-os-mcp-server -n v-os --replicas=5

# Verify
kubectl get pods -n v-os -l app.kubernetes.io/name=v-os-mcp
```

### Horizontal Scaling (Auto)

HPA is configured to:
- Min replicas: 3
- Max replicas: 10
- Scale up at 70% CPU
- Scale up at 80% memory

```bash
# Check HPA status
kubectl get hpa -n v-os

# View HPA details
kubectl describe hpa v-os-mcp-hpa -n v-os
```

### Vertical Scaling

```bash
# Increase resources
kubectl patch deployment v-os-mcp-server -n v-os -p '{
  "spec": {
    "template": {
      "spec": {
        "containers": [{
          "name": "mcp-server",
          "resources": {
            "requests": {"cpu": "500m", "memory": "512Mi"},
            "limits": {"cpu": "2000m", "memory": "2Gi"}
          }
        }]
      }
    }
  }
}'
```

---

## Backup & Recovery

### Redis Backup

```bash
#!/bin/bash
# backup-redis.sh

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="redis-backup-${DATE}.rdb"

# Trigger backup
kubectl exec -n v-os deploy/v-os-redis -- redis-cli BGSAVE

# Wait for backup
sleep 5

# Copy backup file
kubectl cp v-os/v-os-redis-xxx:/data/dump.rdb ./backups/${BACKUP_FILE}

echo "Backup saved to ./backups/${BACKUP_FILE}"
```

### Redis Restore

```bash
#!/bin/bash
# restore-redis.sh <backup-file>

BACKUP_FILE=$1

# Stop Redis
kubectl scale deployment/v-os-redis -n v-os --replicas=0

# Copy backup
kubectl cp ${BACKUP_FILE} v-os/v-os-redis-xxx:/data/dump.rdb

# Start Redis
kubectl scale deployment/v-os-redis -n v-os --replicas=1

echo "Redis restored"
```

---

## Security Operations

### Security Checklist

- [ ] All secrets in Kubernetes Secrets (not ConfigMap)
- [ ] DNS rebinding protection enabled
- [ ] TLS enabled on ingress
- [ ] Network policies in place
- [ ] Pod security context configured
- [ ] Image scanning enabled in CI/CD
- [ ] API keys rotated quarterly

### Security Incident Response

1. **Contain** - Disable affected component
2. **Investigate** - Check logs, traces
3. **Eradicate** - Remove threat
4. **Recover** - Restore service
5. **Document** - Write incident report

```bash
# Emergency: Disable service
kubectl scale deployment/v-os-mcp-server -n v-os --replicas=0

# Block specific IP (if using nginx-ingress)
kubectl annotate ingress v-os-mcp-ingress -n v-os \
  nginx.ingress.kubernetes.io/denylist-source-range="1.2.3.4/32"
```

---

## Contact Information

| Role | Contact | Availability |
|------|---------|--------------|
| On-Call | #v-os-oncall (Slack) | 24/7 |
| Team Lead | @team-lead | Business hours |
| CTO | @cto | Escalation only |
| Security | security@v-os.ai | 24/7 for incidents |

---

*Last Updated: January 2026*
*Version: 1.0*
