// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// Resilience-Matrix F10 — webhook idempotency table queries + mutations.
//
// Composite uniqueness on (provider, externalId). The backend looks up
// `byProviderExternal` after signature verification; matching row =>
// duplicate delivery, short-circuit. Otherwise the handler runs and
// `record` writes a fresh row.

import { query, mutation } from "./_generated/server";
import { v } from "convex/values";

export const byProviderExternal = query({
  args: {
    provider:   v.string(),
    externalId: v.string(),
  },
  handler: async (ctx, { provider, externalId }) => {
    const row = await ctx.db
      .query("webhook_seen")
      .withIndex("by_provider_external", (q) =>
        q.eq("provider", provider).eq("externalId", externalId)
      )
      .first();
    return row;
  },
});

export const record = mutation({
  args: {
    provider:       v.string(),
    externalId:     v.string(),
    eventType:      v.optional(v.string()),
    seenAt:         v.number(),
    expiresAt:      v.number(),
    cachedResponse: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    // Idempotent insert: if the row already exists, leave it untouched
    // so the original `seenAt` reflects the first delivery.
    const existing = await ctx.db
      .query("webhook_seen")
      .withIndex("by_provider_external", (q) =>
        q.eq("provider", args.provider).eq("externalId", args.externalId)
      )
      .first();
    if (existing) return existing._id;
    return await ctx.db.insert("webhook_seen", args);
  },
});

// Scheduled prune — caller (a Convex cron job in convex/crons.ts)
// passes `now` (epoch ms). Rows with expiresAt <= now are deleted in
// batches to avoid runaway transactions.
export const pruneExpired = mutation({
  args: { now: v.number(), batchSize: v.optional(v.number()) },
  handler: async (ctx, { now, batchSize }) => {
    const limit = batchSize ?? 500;
    const rows = await ctx.db
      .query("webhook_seen")
      .withIndex("by_expiry", (q) => q.lt("expiresAt", now))
      .take(limit);
    for (const r of rows) {
      await ctx.db.delete(r._id);
    }
    return rows.length;
  },
});
