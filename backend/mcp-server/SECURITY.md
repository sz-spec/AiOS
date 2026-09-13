# Security Policy - V OS MCP Agent Server

## 🔒 Overview

V OS MCP Agent Server מחויב לאבטחת המשתמשים והמערכות שלהם. מסמך זה מפרט את מדיניות האבטחה, תהליכי דיווח על פגיעויות, וההנחיות ליישום מאובטח.

---

## 📋 Supported Versions

| Version | Supported          | Notes |
| ------- | ------------------ | ----- |
| 1.x.x   | ✅ Active support  | Production ready |
| 0.x.x   | ⚠️ Security fixes only | Migration recommended |
| < 0.5   | ❌ Not supported   | Please upgrade immediately |

### Dependency Security Matrix

| Package | Min Secure Version | CVE Coverage |
|---------|-------------------|--------------|
| `@modelcontextprotocol/sdk` | 1.25.2 | CVE-2025-66414 |
| `fastmcp` | 3.26.8 | Memory leak fixes |
| `zod` | 4.0.0 | Type validation improvements |
| `express` | 4.21.0 | Security patches |

---

## 🚨 Known Vulnerabilities & Mitigations

### CVE-2025-66414: DNS Rebinding Attack

**Severity:** HIGH  
**Affected:** @modelcontextprotocol/sdk < 1.25.0  
**Status:** ✅ Fixed in v1.25.0+

**Description:**  
DNS rebinding vulnerability allows attackers to bypass same-origin policy by manipulating DNS responses, potentially accessing internal MCP server endpoints.

**Mitigation (REQUIRED):**
```typescript
// In your transport configuration
const transport = new StreamableHTTPServerTransport({
  sessionIdGenerator: () => randomUUID(),
  // ⚠️ CRITICAL: Enable DNS rebinding protection
  enableDnsRebindingProtection: true,
  allowedHosts: ['127.0.0.1', 'localhost', 'your-domain.com'],
  allowedOrigins: ['https://your-app.com'],
});
```

**Verification:**
```bash
# Test DNS rebinding protection
curl -H "Host: malicious.attacker.com" http://localhost:8080/mcp
# Should return 403 Forbidden
```

---

## 🔐 Security Best Practices

### 1. Authentication & Authorization

```typescript
// ✅ DO: Implement proper authentication
const server = new FastMCP({
  authenticate: async (request) => {
    const apiKey = request.headers["x-api-key"];
    
    // Validate against secure store (not hardcoded!)
    const isValid = await validateApiKey(apiKey);
    if (!isValid) {
      throw new Response(null, { status: 401 });
    }
    
    return { userId: "...", permissions: [...] };
  },
});

// ✅ DO: Use per-tool authorization
server.addTool({
  name: "sensitive-operation",
  canAccess: (auth) => auth?.permissions.includes("admin"),
  // ...
});

// ❌ DON'T: Hardcode secrets
const apiKey = "sk-1234567890"; // NEVER DO THIS
```

### 2. Input Validation

```typescript
import { z } from "zod";

// ✅ DO: Strict schema validation
const messageSchema = z.object({
  message: z.string()
    .min(1)
    .max(10000)
    .refine(
      (val) => !val.includes("<script>"),
      "Potential XSS detected"
    ),
  agentId: z.string().regex(/^[a-z0-9-]+$/),
});

// ✅ DO: Sanitize file paths
const safeFilePath = z.string().refine(
  (path) => !path.includes("..") && !path.startsWith("/"),
  "Path traversal attempt detected"
);
```

### 3. Rate Limiting

```typescript
// Recommended: Use express-rate-limit or similar
import rateLimit from "express-rate-limit";

const limiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 100, // limit each IP to 100 requests per window
  message: { error: "Too many requests" },
  standardHeaders: true,
  legacyHeaders: false,
});

app.use("/mcp", limiter);
```

### 4. Secure Transport Configuration

```typescript
// ✅ Production configuration
const transport = new StreamableHTTPServerTransport({
  // Session security
  sessionIdGenerator: () => crypto.randomUUID(),
  
  // DNS rebinding protection (CRITICAL)
  enableDnsRebindingProtection: true,
  allowedHosts: process.env.ALLOWED_HOSTS?.split(",") || [],
  allowedOrigins: process.env.ALLOWED_ORIGINS?.split(",") || [],
  
  // SSE configuration (v1.25.1 fix)
  sseOptions: {
    primingEvents: false,
  },
  
  // Timeouts
  requestTimeout: 30000,
  keepAliveTimeout: 60000,
});
```

### 5. Secrets Management

```bash
# ✅ DO: Use environment variables
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export V_OS_API_SECRET="..."

# ✅ DO: Use .env files (gitignored)
# .env.local
OPENAI_API_KEY=sk-...

# ❌ DON'T: Commit secrets to git
# Check: git secrets --scan
```

---

## 🛡️ Security Headers

```typescript
import helmet from "helmet";

app.use(helmet({
  contentSecurityPolicy: {
    directives: {
      defaultSrc: ["'self'"],
      scriptSrc: ["'self'"],
      styleSrc: ["'self'", "'unsafe-inline'"],
      connectSrc: ["'self'", "wss:", "https:"],
    },
  },
  hsts: {
    maxAge: 31536000,
    includeSubDomains: true,
    preload: true,
  },
}));

// CORS for MCP clients
app.use(cors({
  origin: process.env.ALLOWED_ORIGINS?.split(",") || [],
  exposedHeaders: ["Mcp-Session-Id"],
  allowedHeaders: ["Content-Type", "mcp-session-id", "x-api-key"],
  credentials: true,
}));
```

---

## 📝 Reporting a Vulnerability

### How to Report

1. **DO NOT** create a public GitHub issue for security vulnerabilities
2. Email: security@v-os.ai (or your actual security email)
3. Use GitHub's private vulnerability reporting (if enabled)

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact assessment
- Any suggested fixes (optional)

### Response Timeline

| Stage | Timeframe |
|-------|-----------|
| Acknowledgment | 24-48 hours |
| Initial assessment | 72 hours |
| Fix development | 1-2 weeks (critical: 48h) |
| Public disclosure | After fix released |

### Responsible Disclosure

We follow a 90-day disclosure policy. If we fail to address the issue within 90 days, you may disclose it publicly.

---

## 🔄 Security Audit Process

### Automated Scans

```bash
# Run before each release
npm audit
npm audit fix

# Check for outdated packages
npm outdated

# Snyk scanning (if configured)
snyk test
snyk monitor
```

### Manual Review Checklist

- [ ] Authentication bypass attempts
- [ ] Authorization flaws (privilege escalation)
- [ ] Input validation gaps
- [ ] Sensitive data exposure
- [ ] Rate limiting effectiveness
- [ ] Error message information leakage
- [ ] Dependency vulnerabilities
- [ ] DNS rebinding protection enabled
- [ ] CORS configuration review

### Penetration Testing

Recommended annual penetration testing covering:
- MCP protocol fuzzing
- Transport layer attacks
- Authentication/session attacks
- Agent injection attempts

---

## 🔧 Incident Response

### Severity Levels

| Level | Description | Response Time |
|-------|-------------|---------------|
| CRITICAL | Active exploitation, data breach | Immediate (< 4h) |
| HIGH | Exploitable vulnerability | 24 hours |
| MEDIUM | Potential vulnerability | 1 week |
| LOW | Minor security improvement | Next release |

### Response Steps

1. **Identify** - Confirm and assess the vulnerability
2. **Contain** - Limit exposure (disable feature if needed)
3. **Eradicate** - Develop and test fix
4. **Recover** - Deploy fix, verify resolution
5. **Learn** - Post-mortem, update processes

---

## 📚 Security Resources

- [MCP Security Best Practices](https://modelcontextprotocol.io/docs/security)
- [OWASP Top 10](https://owasp.org/Top10/)
- [Node.js Security Checklist](https://nodejs.org/en/docs/guides/security/)
- [CVE Database](https://cve.mitre.org/)

---

## 📅 Security Update Log

| Date | Version | Security Changes |
|------|---------|------------------|
| 2026-01-12 | 1.0.0 | Initial security policy |
| 2026-01-12 | 1.0.0 | DNS rebinding protection enabled |
| 2026-01-12 | 1.0.0 | Updated to SDK 1.25.2 (CVE-2025-66414) |

---

## ✅ Compliance

This project follows:
- **OWASP** security guidelines
- **MCP Specification** security requirements
- **SOC 2** principles (for enterprise deployments)

---

*Last Updated: January 2026*  
*Security Contact: security@v-os.ai*
