import { mutation, query } from "./_generated/server";
import { v } from "convex/values";
import { requireAuth, requireOwnership } from "./authHelpers";

export const getUsage = query({
  args: { userId: v.id("users"), resource: v.string() },
  handler: async (ctx, { userId, resource }) => {
    // Phase v17: Verify caller owns this userId (was requireAuth only)
    await requireOwnership(ctx, userId);
    return ctx.db
      .query("quotaUsage")
      .withIndex("by_user_resource", (q) =>
        q.eq("userId", userId).eq("resource", resource)
      )
      .unique();
  },
});

export const checkQuota = query({
  args: { userId: v.id("users"), resource: v.string() },
  handler: async (ctx, { userId, resource }) => {
    // Phase v17: Verify caller owns this userId (was requireAuth only)
    await requireOwnership(ctx, userId);
    const usage = await ctx.db
      .query("quotaUsage")
      .withIndex("by_user_resource", (q) =>
        q.eq("userId", userId).eq("resource", resource)
      )
      .unique();

    if (!usage) return { allowed: true, remaining: null };

    const remaining = usage.limit - usage.used;
    return { allowed: remaining > 0, remaining, used: usage.used, limit: usage.limit };
  },
});

export const recordUsage = mutation({
  args: {
    userId: v.id("users"),
    resource: v.string(),
    delta: v.number(),
    organizationId: v.optional(v.id("organizations")),
    reason: v.optional(v.string()),
  },
  handler: async (ctx, { userId, resource, delta, organizationId, reason }) => {
    await requireOwnership(ctx, userId);
    const existing = await ctx.db
      .query("quotaUsage")
      .withIndex("by_user_resource", (q) =>
        q.eq("userId", userId).eq("resource", resource)
      )
      .unique();

    if (existing) {
      await ctx.db.patch(existing._id, { used: existing.used + delta });
    }

    await ctx.db.insert("quotaTransactions", {
      userId,
      resource,
      delta,
      reason,
    });

    return true;
  },
});

export const setQuotaLimit = mutation({
  args: {
    userId: v.id("users"),
    resource: v.string(),
    limit: v.number(),
    periodStart: v.number(),
    periodEnd: v.number(),
    organizationId: v.optional(v.id("organizations")),
  },
  handler: async (ctx, args) => {
    await requireOwnership(ctx, args.userId);
    const existing = await ctx.db
      .query("quotaUsage")
      .withIndex("by_user_resource", (q) =>
        q.eq("userId", args.userId).eq("resource", args.resource)
      )
      .unique();

    if (existing) {
      await ctx.db.patch(existing._id, {
        limit: args.limit,
        periodStart: args.periodStart,
        periodEnd: args.periodEnd,
      });
      return existing._id;
    }

    return ctx.db.insert("quotaUsage", {
      userId: args.userId,
      resource: args.resource,
      used: 0,
      limit: args.limit,
      periodStart: args.periodStart,
      periodEnd: args.periodEnd,
      organizationId: args.organizationId,
    });
  },
});

export const createAlert = mutation({
  args: {
    userId: v.id("users"),
    resource: v.string(),
    threshold: v.number(),
  },
  handler: async (ctx, args) => {
    await requireOwnership(ctx, args.userId);
    return ctx.db.insert("quotaAlerts", { ...args, triggered: false });
  },
});

export const listAlerts = query({
  args: { userId: v.id("users") },
  handler: async (ctx, { userId }) => {
    // Phase v17: Verify caller owns this userId (was requireAuth only)
    await requireOwnership(ctx, userId);
    // W3.2c — per-user alerts; defensive cap.
    return ctx.db
      .query("quotaAlerts")
      .withIndex("by_user", (q) => q.eq("userId", userId))
      .take(100);
  },
});
