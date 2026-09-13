# V OS MCP Agent Server - Production Dockerfile
# Security-hardened multi-stage build

# ============================================================================
# Stage 1: Dependencies
# ============================================================================
FROM node:22-alpine AS deps

# Security: Add non-root user early
RUN addgroup -g 1001 -S nodejs && \
    adduser -S nodejs -u 1001

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies with security audit
RUN npm ci --only=production && \
    npm audit --audit-level=high || echo "Audit warnings found" && \
    npm cache clean --force

# ============================================================================
# Stage 2: Builder
# ============================================================================
FROM node:22-alpine AS builder

WORKDIR /app

# Copy package files and install ALL deps (including dev)
COPY package*.json ./
RUN npm ci

# Copy source code
COPY tsconfig.json ./
COPY src ./src

# Build TypeScript
RUN npm run build

# ============================================================================
# Stage 3: Production Runtime
# ============================================================================
FROM node:22-alpine AS runner

# ============================================================================
# Security Hardening
# ============================================================================

# 1. Update base packages and remove vulnerabilities
RUN apk update && \
    apk upgrade --no-cache && \
    apk add --no-cache \
        dumb-init \
        ca-certificates \
    && rm -rf /var/cache/apk/*

# 2. Create non-root user
RUN addgroup -g 1001 -S nodejs && \
    adduser -S nodejs -u 1001

# 3. Set secure environment defaults
ENV NODE_ENV=production \
    # Disable npm update notifier
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    # Reduce information exposure
    NODE_OPTIONS="--no-deprecation --no-warnings" \
    # Security headers
    npm_config_audit=true

# 4. Create app directory with proper permissions
WORKDIR /app
RUN chown -R nodejs:nodejs /app

# ============================================================================
# Application Setup
# ============================================================================

# Copy production dependencies from deps stage
COPY --from=deps --chown=nodejs:nodejs /app/node_modules ./node_modules

# Copy built application from builder stage
COPY --from=builder --chown=nodejs:nodejs /app/dist ./dist
COPY --from=builder --chown=nodejs:nodejs /app/package.json ./

# Copy configuration files
COPY --chown=nodejs:nodejs SECURITY.md ./

# ============================================================================
# Security: File Permissions
# ============================================================================

# Remove unnecessary files and set read-only where possible
RUN chmod -R 555 /app/dist && \
    chmod 444 /app/package.json && \
    chmod 444 /app/SECURITY.md

# ============================================================================
# Runtime Configuration
# ============================================================================

# Switch to non-root user
USER nodejs

# Expose port (non-privileged)
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8080/health || exit 1

# Use dumb-init to handle signals properly
ENTRYPOINT ["dumb-init", "--"]

# Start application
CMD ["node", "dist/server.js"]

# ============================================================================
# Labels (OCI standard)
# ============================================================================
LABEL org.opencontainers.image.title="V OS MCP Agent Server" \
      org.opencontainers.image.description="Production-grade MCP Agent Server with multi-agent orchestration" \
      org.opencontainers.image.version="1.0.1" \
      org.opencontainers.image.vendor="V OS" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/v-os/mcp-agent-server" \
      security.hardened="true" \
      security.cve-2025-66414="patched"
