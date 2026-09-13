// v-os-mcp-agent-server/src/framework/caching/tool-registry-cache.ts
// V OS MCP Agent Server - Advanced Caching Strategy
// Based on FastMCP v3.26.8 commit #222 improvements

import { LRUCache } from "lru-cache";
import { createHash } from "crypto";

// ============================================================================
// TYPES & INTERFACES
// ============================================================================

interface CacheConfig {
  enabled: boolean;
  /** TTL in milliseconds */
  ttl: number;
  /** Maximum number of cached items */
  maxSize: number;
  /** Enable cache statistics */
  enableStats: boolean;
  /** Stale-while-revalidate window (ms) */
  staleWhileRevalidate?: number;
}

interface CacheEntry<T> {
  data: T;
  createdAt: number;
  accessCount: number;
  lastAccessed: number;
}

interface CacheStats {
  hits: number;
  misses: number;
  hitRate: number;
  evictions: number;
  size: number;
  avgAccessTime: number;
}

interface ToolCacheKey {
  toolName: string;
  argsHash: string;
  sessionId?: string;
}

// ============================================================================
// CACHE LAYERS ARCHITECTURE
// ============================================================================

/**
 * V OS מיישם 3 שכבות caching:
 * 
 * ┌─────────────────────────────────────────────────────────────┐
 * │                    L1: Request Cache                        │
 * │         (In-memory, per-request, TTL: request lifetime)    │
 * ├─────────────────────────────────────────────────────────────┤
 * │                    L2: Session Cache                        │
 * │         (In-memory LRU, per-session, TTL: 5 minutes)       │
 * ├─────────────────────────────────────────────────────────────┤
 * │                    L3: Global Cache                         │
 * │         (Redis/Memory, shared, TTL: 15 minutes)            │
 * └─────────────────────────────────────────────────────────────┘
 * 
 * Cache Lookup Flow:
 * Request → L1 (hit?) → L2 (hit?) → L3 (hit?) → Execute Tool → Cache
 */

// ============================================================================
// L1: REQUEST-SCOPED CACHE
// ============================================================================

/**
 * Request-level cache - lives only for the duration of a single request
 * Perfect for avoiding duplicate tool calls within a single MCP request
 */
export class RequestCache {
  private cache = new Map<string, any>();

  get<T>(key: string): T | undefined {
    return this.cache.get(key);
  }

  set<T>(key: string, value: T): void {
    this.cache.set(key, value);
  }

  has(key: string): boolean {
    return this.cache.has(key);
  }

  clear(): void {
    this.cache.clear();
  }
}

// ============================================================================
// L2: SESSION-SCOPED LRU CACHE
// ============================================================================

/**
 * Session-level LRU cache with TTL
 * Based on FastMCP commit #222 list handler caching
 */
export class SessionCache<T> {
  private cache: LRUCache<string, CacheEntry<T>>;
  private stats: CacheStats = {
    hits: 0,
    misses: 0,
    hitRate: 0,
    evictions: 0,
    size: 0,
    avgAccessTime: 0,
  };
  private accessTimes: number[] = [];

  constructor(private config: CacheConfig) {
    this.cache = new LRUCache<string, CacheEntry<T>>({
      max: config.maxSize,
      ttl: config.ttl,
      updateAgeOnGet: true,
      dispose: () => {
        this.stats.evictions++;
      },
    });
  }

  async get(key: string): Promise<T | undefined> {
    const startTime = Date.now();
    const entry = this.cache.get(key);

    if (entry) {
      this.stats.hits++;
      entry.accessCount++;
      entry.lastAccessed = Date.now();
      this.recordAccessTime(Date.now() - startTime);
      return entry.data;
    }

    this.stats.misses++;
    this.updateHitRate();
    return undefined;
  }

  async set(key: string, data: T): Promise<void> {
    const entry: CacheEntry<T> = {
      data,
      createdAt: Date.now(),
      accessCount: 0,
      lastAccessed: Date.now(),
    };
    this.cache.set(key, entry);
    this.stats.size = this.cache.size;
  }

  async getOrSet(key: string, fetchFn: () => Promise<T>): Promise<T> {
    const cached = await this.get(key);
    if (cached !== undefined) {
      return cached;
    }

    const data = await fetchFn();
    await this.set(key, data);
    return data;
  }

  /**
   * Stale-while-revalidate pattern
   * Returns stale data immediately while refreshing in background
   */
  async getStaleWhileRevalidate(
    key: string,
    fetchFn: () => Promise<T>
  ): Promise<T> {
    const entry = this.cache.get(key);
    
    if (entry) {
      const age = Date.now() - entry.createdAt;
      const isStale = age > this.config.ttl;
      const withinRevalidateWindow = 
        this.config.staleWhileRevalidate && 
        age < this.config.ttl + this.config.staleWhileRevalidate;

      if (!isStale) {
        // Fresh data - return immediately
        return entry.data;
      }

      if (withinRevalidateWindow) {
        // Stale but within window - return stale, refresh in background
        this.refreshInBackground(key, fetchFn);
        return entry.data;
      }
    }

    // No data or too stale - fetch synchronously
    const data = await fetchFn();
    await this.set(key, data);
    return data;
  }

  private async refreshInBackground(
    key: string,
    fetchFn: () => Promise<T>
  ): Promise<void> {
    // Fire and forget - don't await
    fetchFn()
      .then((data) => this.set(key, data))
      .catch((err) => console.error(`Background refresh failed for ${key}:`, err));
  }

  private recordAccessTime(time: number): void {
    this.accessTimes.push(time);
    if (this.accessTimes.length > 1000) {
      this.accessTimes.shift();
    }
    this.stats.avgAccessTime = 
      this.accessTimes.reduce((a, b) => a + b, 0) / this.accessTimes.length;
  }

  private updateHitRate(): void {
    const total = this.stats.hits + this.stats.misses;
    this.stats.hitRate = total > 0 ? this.stats.hits / total : 0;
  }

  getStats(): CacheStats {
    return { ...this.stats };
  }

  clear(): void {
    this.cache.clear();
    this.stats.size = 0;
  }

  invalidate(key: string): boolean {
    return this.cache.delete(key);
  }

  /**
   * Invalidate all keys matching a pattern
   */
  invalidatePattern(pattern: RegExp): number {
    let count = 0;
    for (const key of this.cache.keys()) {
      if (pattern.test(key)) {
        this.cache.delete(key);
        count++;
      }
    }
    return count;
  }
}

// ============================================================================
// L3: GLOBAL CACHE (Redis-backed or In-Memory)
// ============================================================================

interface GlobalCacheProvider {
  get(key: string): Promise<string | null>;
  set(key: string, value: string, ttlSeconds: number): Promise<void>;
  del(key: string): Promise<void>;
  keys(pattern: string): Promise<string[]>;
}

/**
 * In-memory implementation for development/single-instance deployments
 */
class InMemoryGlobalCache implements GlobalCacheProvider {
  private cache = new Map<string, { value: string; expiresAt: number }>();

  async get(key: string): Promise<string | null> {
    const entry = this.cache.get(key);
    if (!entry) return null;
    if (Date.now() > entry.expiresAt) {
      this.cache.delete(key);
      return null;
    }
    return entry.value;
  }

  async set(key: string, value: string, ttlSeconds: number): Promise<void> {
    this.cache.set(key, {
      value,
      expiresAt: Date.now() + ttlSeconds * 1000,
    });
  }

  async del(key: string): Promise<void> {
    this.cache.delete(key);
  }

  async keys(pattern: string): Promise<string[]> {
    const regex = new RegExp(pattern.replace("*", ".*"));
    return Array.from(this.cache.keys()).filter((k) => regex.test(k));
  }
}

/**
 * Redis implementation for distributed deployments
 */
class RedisGlobalCache implements GlobalCacheProvider {
  constructor(private redis: any) {} // ioredis instance

  async get(key: string): Promise<string | null> {
    return this.redis.get(key);
  }

  async set(key: string, value: string, ttlSeconds: number): Promise<void> {
    await this.redis.setex(key, ttlSeconds, value);
  }

  async del(key: string): Promise<void> {
    await this.redis.del(key);
  }

  async keys(pattern: string): Promise<string[]> {
    return this.redis.keys(pattern);
  }
}

// ============================================================================
// TOOL REGISTRY CACHE MANAGER
// ============================================================================

/**
 * Main cache manager for V OS Tool Registry
 * Implements multi-layer caching with automatic invalidation
 */
export class ToolRegistryCacheManager {
  private sessionCaches = new Map<string, SessionCache<any>>();
  private globalCache: GlobalCacheProvider;
  private config: CacheConfig;

  // Cache key prefixes
  private readonly KEY_PREFIX = {
    TOOL_LIST: "vos:tools:list",
    TOOL_RESULT: "vos:tools:result",
    AGENT_LIST: "vos:agents:list",
    RESOURCE_LIST: "vos:resources:list",
    CONTEXT: "vos:context",
  };

  constructor(
    config: Partial<CacheConfig> = {},
    redisClient?: any
  ) {
    this.config = {
      enabled: true,
      ttl: 5 * 60 * 1000, // 5 minutes
      maxSize: 1000,
      enableStats: true,
      staleWhileRevalidate: 60 * 1000, // 1 minute
      ...config,
    };

    this.globalCache = redisClient
      ? new RedisGlobalCache(redisClient)
      : new InMemoryGlobalCache();
  }

  // -------------------------------------------------------------------------
  // Session Cache Management
  // -------------------------------------------------------------------------

  private getSessionCache(sessionId: string): SessionCache<any> {
    if (!this.sessionCaches.has(sessionId)) {
      this.sessionCaches.set(
        sessionId,
        new SessionCache(this.config)
      );
    }
    return this.sessionCaches.get(sessionId)!;
  }

  clearSessionCache(sessionId: string): void {
    this.sessionCaches.get(sessionId)?.clear();
    this.sessionCaches.delete(sessionId);
  }

  // -------------------------------------------------------------------------
  // Tool List Caching (from FastMCP #222)
  // -------------------------------------------------------------------------

  /**
   * Cache tool list results
   * High hit rate expected - tool lists rarely change
   */
  async getCachedToolList(
    sessionId: string,
    fetchFn: () => Promise<any[]>
  ): Promise<any[]> {
    const cache = this.getSessionCache(sessionId);
    const key = `${this.KEY_PREFIX.TOOL_LIST}:${sessionId}`;

    return cache.getStaleWhileRevalidate(key, fetchFn);
  }

  /**
   * Invalidate tool list when tools are added/removed
   */
  invalidateToolList(sessionId?: string): void {
    if (sessionId) {
      this.getSessionCache(sessionId).invalidatePattern(
        new RegExp(`^${this.KEY_PREFIX.TOOL_LIST}`)
      );
    } else {
      // Invalidate for all sessions
      for (const cache of this.sessionCaches.values()) {
        cache.invalidatePattern(new RegExp(`^${this.KEY_PREFIX.TOOL_LIST}`));
      }
    }
  }

  // -------------------------------------------------------------------------
  // Tool Result Caching
  // -------------------------------------------------------------------------

  /**
   * Generate cache key for tool execution results
   */
  private generateToolResultKey(
    toolName: string,
    args: Record<string, any>,
    sessionId?: string
  ): string {
    const argsHash = createHash("sha256")
      .update(JSON.stringify(args))
      .digest("hex")
      .substring(0, 16);

    return `${this.KEY_PREFIX.TOOL_RESULT}:${toolName}:${argsHash}${
      sessionId ? `:${sessionId}` : ""
    }`;
  }

  /**
   * Cache tool execution results
   * Configurable per-tool caching strategy
   */
  async getCachedToolResult<T>(
    toolName: string,
    args: Record<string, any>,
    sessionId: string,
    fetchFn: () => Promise<T>,
    options?: {
      /** Override default TTL for this tool */
      ttl?: number;
      /** Whether this result is session-specific */
      sessionScoped?: boolean;
      /** Custom cache key generator */
      cacheKeyFn?: (args: Record<string, any>) => string;
    }
  ): Promise<T> {
    if (!this.config.enabled) {
      return fetchFn();
    }

    const key = options?.cacheKeyFn
      ? options.cacheKeyFn(args)
      : this.generateToolResultKey(
          toolName,
          args,
          options?.sessionScoped ? sessionId : undefined
        );

    // Try session cache first (L2)
    const sessionCache = this.getSessionCache(sessionId);
    const sessionCached = await sessionCache.get(key);
    if (sessionCached !== undefined) {
      return sessionCached as T;
    }

    // Try global cache (L3)
    const globalCached = await this.globalCache.get(key);
    if (globalCached) {
      const data = JSON.parse(globalCached) as T;
      // Populate session cache
      await sessionCache.set(key, data);
      return data;
    }

    // Cache miss - execute and cache
    const result = await fetchFn();

    // Store in both caches
    await sessionCache.set(key, result);
    const ttlSeconds = (options?.ttl || this.config.ttl) / 1000;
    await this.globalCache.set(key, JSON.stringify(result), ttlSeconds);

    return result;
  }

  // -------------------------------------------------------------------------
  // Agent Routing Cache
  // -------------------------------------------------------------------------

  /**
   * Cache agent routing decisions
   * Helps avoid repeated intent classification
   */
  async getCachedAgentRoute(
    message: string,
    sessionId: string,
    routeFn: () => Promise<string>
  ): Promise<string> {
    // Use message hash as key (first 100 chars + hash)
    const messageKey = createHash("md5")
      .update(message.substring(0, 500))
      .digest("hex");

    const key = `vos:route:${messageKey}`;
    const cache = this.getSessionCache(sessionId);

    return cache.getOrSet(key, routeFn);
  }

  // -------------------------------------------------------------------------
  // Context Caching
  // -------------------------------------------------------------------------

  /**
   * Cache context lookups
   */
  async getCachedContext(
    sessionId: string,
    contextKey: string,
    fetchFn: () => Promise<any>
  ): Promise<any> {
    const key = `${this.KEY_PREFIX.CONTEXT}:${sessionId}:${contextKey}`;
    const cache = this.getSessionCache(sessionId);

    return cache.getOrSet(key, fetchFn);
  }

  invalidateContext(sessionId: string, contextKey?: string): void {
    const cache = this.getSessionCache(sessionId);
    if (contextKey) {
      cache.invalidate(`${this.KEY_PREFIX.CONTEXT}:${sessionId}:${contextKey}`);
    } else {
      cache.invalidatePattern(
        new RegExp(`^${this.KEY_PREFIX.CONTEXT}:${sessionId}`)
      );
    }
  }

  // -------------------------------------------------------------------------
  // Statistics & Monitoring
  // -------------------------------------------------------------------------

  /**
   * Get cache statistics for monitoring
   */
  getStats(sessionId?: string): {
    sessions: number;
    sessionStats?: CacheStats;
    aggregateStats: {
      totalHits: number;
      totalMisses: number;
      avgHitRate: number;
    };
  } {
    const sessions = this.sessionCaches.size;
    
    let totalHits = 0;
    let totalMisses = 0;
    
    for (const cache of this.sessionCaches.values()) {
      const stats = cache.getStats();
      totalHits += stats.hits;
      totalMisses += stats.misses;
    }

    const total = totalHits + totalMisses;

    return {
      sessions,
      sessionStats: sessionId
        ? this.sessionCaches.get(sessionId)?.getStats()
        : undefined,
      aggregateStats: {
        totalHits,
        totalMisses,
        avgHitRate: total > 0 ? totalHits / total : 0,
      },
    };
  }

  /**
   * Export metrics for Prometheus/OpenTelemetry
   */
  getMetrics(): Record<string, number> {
    const stats = this.getStats();
    return {
      "vos_cache_sessions_total": stats.sessions,
      "vos_cache_hits_total": stats.aggregateStats.totalHits,
      "vos_cache_misses_total": stats.aggregateStats.totalMisses,
      "vos_cache_hit_rate": stats.aggregateStats.avgHitRate,
    };
  }
}

// ============================================================================
// INTEGRATION WITH FASTMCP
// ============================================================================

/**
 * Example integration with FastMCP server
 */
export function createCachedToolRegistry(
  server: any, // FastMCP instance
  cacheManager: ToolRegistryCacheManager
) {
  // Wrap addTool to support caching
  const originalAddTool = server.addTool.bind(server);

  server.addTool = (toolConfig: any) => {
    const { execute, cache: cacheOptions, ...rest } = toolConfig;

    if (cacheOptions?.enabled === false) {
      // No caching for this tool
      return originalAddTool({ ...rest, execute });
    }

    // Wrap execute with caching
    const cachedExecute = async (args: any, context: any) => {
      const sessionId = context.session?.userId || "default";

      return cacheManager.getCachedToolResult(
        rest.name,
        args,
        sessionId,
        () => execute(args, context),
        {
          ttl: cacheOptions?.ttl,
          sessionScoped: cacheOptions?.sessionScoped,
          cacheKeyFn: cacheOptions?.cacheKeyFn,
        }
      );
    };

    return originalAddTool({ ...rest, execute: cachedExecute });
  };

  // Listen for tool changes to invalidate cache
  server.on("toolAdded", () => cacheManager.invalidateToolList());
  server.on("toolRemoved", () => cacheManager.invalidateToolList());
  server.on("toolUpdated", () => cacheManager.invalidateToolList());

  return server;
}

// ============================================================================
// USAGE EXAMPLE
// ============================================================================

/*
import { FastMCP } from "fastmcp";
import { ToolRegistryCacheManager, createCachedToolRegistry } from "./caching";

const cacheManager = new ToolRegistryCacheManager({
  enabled: true,
  ttl: 5 * 60 * 1000,
  maxSize: 1000,
  enableStats: true,
});

const server = new FastMCP({ name: "V OS", version: "1.0.0" });
createCachedToolRegistry(server, cacheManager);

// Tool with custom caching
server.addTool({
  name: "v_agent_list",
  cache: {
    enabled: true,
    ttl: 10 * 60 * 1000, // 10 minutes - agent list rarely changes
    sessionScoped: false, // Same for all sessions
  },
  execute: async () => {
    // ... fetch agents
  },
});

// Tool with session-specific caching
server.addTool({
  name: "v_context_get",
  cache: {
    enabled: true,
    sessionScoped: true,
    cacheKeyFn: (args) => `context:${args.key}`,
  },
  execute: async (args, context) => {
    // ... fetch context
  },
});

// Tool without caching (side effects)
server.addTool({
  name: "v_agent_chat",
  cache: { enabled: false }, // Never cache chat responses
  execute: async (args, context) => {
    // ... streaming chat
  },
});

// Expose cache stats endpoint
server.addTool({
  name: "v_cache_stats",
  execute: async (_, { session }) => {
    const stats = cacheManager.getStats(session?.userId);
    return JSON.stringify(stats, null, 2);
  },
});
*/

export { RequestCache, GlobalCacheProvider, InMemoryGlobalCache, RedisGlobalCache };
