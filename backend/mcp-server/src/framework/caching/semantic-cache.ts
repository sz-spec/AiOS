// src/framework/caching/semantic-cache.ts
// Semantic Caching for Similar Queries

import { OpenAI } from "openai";
import { createHash } from "crypto";

// ============================================================================
// TYPES
// ============================================================================

export interface SemanticCacheConfig {
  similarityThreshold: number;       // 0.95 = 95% similar required for hit
  embeddingModel: string;            // "text-embedding-3-small"
  maxCacheSize: number;              // Maximum entries
  ttlMs: number;                     // Time to live in milliseconds
  enabled: boolean;
}

export interface CachedEntry {
  id: string;
  query: string;
  queryHash: string;
  embedding: number[];
  response: string;
  model: string;
  tokens: {
    input: number;
    output: number;
  };
  cost: number;
  createdAt: number;
  hits: number;
  lastHitAt: number;
}

export interface CacheResult {
  hit: boolean;
  entry?: CachedEntry;
  similarity?: number;
  savedCost?: number;
}

export interface CacheStats {
  totalEntries: number;
  hits: number;
  misses: number;
  hitRate: number;
  totalSavedCost: number;
  avgSimilarity: number;
  oldestEntry: number;
  newestEntry: number;
}

// ============================================================================
// VECTOR SIMILARITY
// ============================================================================

function cosineSimilarity(a: number[], b: number[]): number {
  if (a.length !== b.length) {
    throw new Error("Vectors must have the same length");
  }

  let dotProduct = 0;
  let normA = 0;
  let normB = 0;

  for (let i = 0; i < a.length; i++) {
    dotProduct += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }

  if (normA === 0 || normB === 0) return 0;
  
  return dotProduct / (Math.sqrt(normA) * Math.sqrt(normB));
}

// ============================================================================
// SEMANTIC CACHE
// ============================================================================

export class SemanticCache {
  private openai: OpenAI;
  private cache: Map<string, CachedEntry> = new Map();
  private embeddings: Map<string, number[]> = new Map();
  private stats = {
    hits: 0,
    misses: 0,
    totalSavedCost: 0,
    similarities: [] as number[],
  };

  private config: SemanticCacheConfig = {
    similarityThreshold: 0.92,
    embeddingModel: "text-embedding-3-small",
    maxCacheSize: 10000,
    ttlMs: 24 * 60 * 60 * 1000, // 24 hours
    enabled: true,
  };

  constructor(openaiApiKey: string, config?: Partial<SemanticCacheConfig>) {
    this.openai = new OpenAI({ apiKey: openaiApiKey });
    if (config) {
      this.config = { ...this.config, ...config };
    }
  }

  /**
   * Try to get a cached response for a similar query
   */
  async get(query: string): Promise<CacheResult> {
    if (!this.config.enabled) {
      return { hit: false };
    }

    // 1. Check exact hash match first (fast path)
    const queryHash = this.hashQuery(query);
    const exactMatch = this.cache.get(queryHash);
    
    if (exactMatch && !this.isExpired(exactMatch)) {
      exactMatch.hits++;
      exactMatch.lastHitAt = Date.now();
      this.stats.hits++;
      this.stats.totalSavedCost += exactMatch.cost;
      
      return {
        hit: true,
        entry: exactMatch,
        similarity: 1.0,
        savedCost: exactMatch.cost,
      };
    }

    // 2. Generate embedding for semantic search
    const queryEmbedding = await this.generateEmbedding(query);

    // 3. Find most similar cached query
    let bestMatch: { entry: CachedEntry; similarity: number } | null = null;

    for (const [id, entry] of this.cache.entries()) {
      if (this.isExpired(entry)) {
        this.cache.delete(id);
        this.embeddings.delete(id);
        continue;
      }

      const entryEmbedding = this.embeddings.get(id);
      if (!entryEmbedding) continue;

      const similarity = cosineSimilarity(queryEmbedding, entryEmbedding);

      if (similarity >= this.config.similarityThreshold) {
        if (!bestMatch || similarity > bestMatch.similarity) {
          bestMatch = { entry, similarity };
        }
      }
    }

    // 4. Return result
    if (bestMatch) {
      bestMatch.entry.hits++;
      bestMatch.entry.lastHitAt = Date.now();
      this.stats.hits++;
      this.stats.totalSavedCost += bestMatch.entry.cost;
      this.stats.similarities.push(bestMatch.similarity);

      return {
        hit: true,
        entry: bestMatch.entry,
        similarity: bestMatch.similarity,
        savedCost: bestMatch.entry.cost,
      };
    }

    this.stats.misses++;
    return { hit: false };
  }

  /**
   * Cache a response for future similar queries
   */
  async set(
    query: string,
    response: string,
    metadata: {
      model: string;
      inputTokens: number;
      outputTokens: number;
      cost: number;
    }
  ): Promise<void> {
    if (!this.config.enabled) return;

    // Enforce cache size limit
    if (this.cache.size >= this.config.maxCacheSize) {
      this.evictOldest();
    }

    const queryHash = this.hashQuery(query);
    const embedding = await this.generateEmbedding(query);

    const entry: CachedEntry = {
      id: queryHash,
      query,
      queryHash,
      embedding,
      response,
      model: metadata.model,
      tokens: {
        input: metadata.inputTokens,
        output: metadata.outputTokens,
      },
      cost: metadata.cost,
      createdAt: Date.now(),
      hits: 0,
      lastHitAt: Date.now(),
    };

    this.cache.set(queryHash, entry);
    this.embeddings.set(queryHash, embedding);
  }

  /**
   * Generate embedding for a query
   */
  private async generateEmbedding(text: string): Promise<number[]> {
    try {
      const response = await this.openai.embeddings.create({
        model: this.config.embeddingModel,
        input: text.slice(0, 8000), // Limit input size
      });
      return response.data[0].embedding;
    } catch (error) {
      console.error("Failed to generate embedding:", error);
      // Return zero vector on error (won't match anything)
      return new Array(1536).fill(0);
    }
  }

  /**
   * Hash a query for exact matching
   */
  private hashQuery(query: string): string {
    const normalized = query.toLowerCase().trim().replace(/\s+/g, " ");
    return createHash("sha256").update(normalized).digest("hex").slice(0, 16);
  }

  /**
   * Check if entry is expired
   */
  private isExpired(entry: CachedEntry): boolean {
    return Date.now() - entry.createdAt > this.config.ttlMs;
  }

  /**
   * Evict oldest/least used entries
   */
  private evictOldest(): void {
    // Sort by (hits * recency) score
    const entries = Array.from(this.cache.entries()).map(([id, entry]) => ({
      id,
      entry,
      score: entry.hits * (1 / (Date.now() - entry.lastHitAt)),
    }));

    entries.sort((a, b) => a.score - b.score);

    // Remove bottom 10%
    const toRemove = Math.ceil(entries.length * 0.1);
    for (let i = 0; i < toRemove; i++) {
      this.cache.delete(entries[i].id);
      this.embeddings.delete(entries[i].id);
    }
  }

  /**
   * Get cache statistics
   */
  getStats(): CacheStats {
    const entries = Array.from(this.cache.values());
    const timestamps = entries.map((e) => e.createdAt);

    return {
      totalEntries: this.cache.size,
      hits: this.stats.hits,
      misses: this.stats.misses,
      hitRate:
        this.stats.hits + this.stats.misses > 0
          ? this.stats.hits / (this.stats.hits + this.stats.misses)
          : 0,
      totalSavedCost: this.stats.totalSavedCost,
      avgSimilarity:
        this.stats.similarities.length > 0
          ? this.stats.similarities.reduce((a, b) => a + b, 0) /
            this.stats.similarities.length
          : 0,
      oldestEntry: timestamps.length > 0 ? Math.min(...timestamps) : 0,
      newestEntry: timestamps.length > 0 ? Math.max(...timestamps) : 0,
    };
  }

  /**
   * Clear all cached entries
   */
  clear(): void {
    this.cache.clear();
    this.embeddings.clear();
    this.stats = {
      hits: 0,
      misses: 0,
      totalSavedCost: 0,
      similarities: [],
    };
  }

  /**
   * Export cache for persistence (without embeddings for size)
   */
  export(): CachedEntry[] {
    return Array.from(this.cache.values());
  }

  /**
   * Import cached entries
   */
  async import(entries: CachedEntry[]): Promise<void> {
    for (const entry of entries) {
      if (!this.isExpired(entry)) {
        this.cache.set(entry.id, entry);
        // Regenerate embedding if not present
        if (!entry.embedding || entry.embedding.length === 0) {
          const embedding = await this.generateEmbedding(entry.query);
          this.embeddings.set(entry.id, embedding);
        } else {
          this.embeddings.set(entry.id, entry.embedding);
        }
      }
    }
  }
}

// ============================================================================
// REDIS-BACKED SEMANTIC CACHE (for distributed systems)
// ============================================================================

export class RedisSemanticCache extends SemanticCache {
  // TODO: Implement Redis-backed version for production
  // Uses Redis for cache storage and Pinecone/Milvus for vector search
}

// ============================================================================
// EXPORTS
// ============================================================================

export function createSemanticCache(
  openaiApiKey: string,
  config?: Partial<SemanticCacheConfig>
): SemanticCache {
  return new SemanticCache(openaiApiKey, config);
}
