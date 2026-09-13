import { describe, expect, it } from 'vitest';
import { CostAwareRouter, RoutingDecisionSchema } from '../../src/orchestration/cost-aware-router.js';
import { QuotaManager } from '../../src/framework/quotas/quota-manager.js';

describe('dependency migration behavior', () => {
  it('routes actual requests through each supported model and validates their Zod 4 output', async () => {
    const router = new CostAwareRouter();
    for (const forceModel of ['liquid-lfm', 'claude-opus', 'gpt-5.2-pro', 'mistral-7b']) {
      const result = await router.route('hello', { forceModel });
      expect(result.reason).toBe('forced_model');
      expect(RoutingDecisionSchema.safeParse(result).success).toBe(true);
    }
  });
  it('rejects invalid providers and numeric fields under Zod 4', async () => {
    const result = await new CostAwareRouter().route('hello');
    expect(RoutingDecisionSchema.safeParse({ ...result, model: { ...result.model, provider: 'untrusted' } }).success).toBe(false);
    expect(RoutingDecisionSchema.safeParse({ ...result, estimatedCost: '0' }).success).toBe(false);
    expect(RoutingDecisionSchema.safeParse({ ...result, estimatedCost: Number.NaN }).success).toBe(false);
  });
  it('enforces real per-request quota limits', async () => {
    const manager = new QuotaManager();
    const limit = manager.getQuota('migration-test').perRequest;
    expect((await manager.checkQuota('migration-test', {tokens: 1, cost: 0})).allowed).toBe(true);
    expect((await manager.checkQuota('migration-test', {tokens: limit.maxTokens + 1, cost: 0})).allowed).toBe(false);
    expect((await manager.checkQuota('migration-test', {tokens: 1, cost: limit.maxCost + 1})).allowed).toBe(false);
  });
});
