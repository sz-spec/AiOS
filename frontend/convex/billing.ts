import { mutation, query, internalMutation } from "./_generated/server";
import { v } from "convex/values";
import { ConvexError } from "convex/values";
import { requireAuth, requireOwnership } from "./authHelpers";

// ─── Credits / Balance ────────────────────────────────────────────────────

export const getBalance = query({
  args: { userId: v.id("users") },
  handler: async (ctx, { userId }) => {
    await requireOwnership(ctx, userId);
    const credits = await ctx.db
      .query("userCredits")
      .withIndex("by_user", (q) => q.eq("userId", userId))
      .unique();
    return credits?.balance ?? 0;
  },
});

// MC-1 (v20.6.2): addCredits is callable only via Stripe-webhook backend.
// Migrated mutation → internalMutation so client-side useMutation cannot
// invoke it directly. Reachable via ctx.runMutation(internal.billing.addCredits).
// Verified zero frontend callers via grep on api.billing.addCredits.
export const addCredits = internalMutation({
  args: {
    userId: v.id("users"),
    amount: v.number(),
    reason: v.string(),
    metadata: v.optional(v.any()),
  },
  handler: async (ctx, { userId, amount, reason, metadata }) => {
    // Phase v17 (F-C2): Hard bounds check — prevent abuse via extreme values
    if (amount <= 0 || amount > 10000) {
      throw new ConvexError("Invalid credit amount: must be between 1 and 10,000");
    }

    await requireOwnership(ctx, userId);
    const existing = await ctx.db
      .query("userCredits")
      .withIndex("by_user", (q) => q.eq("userId", userId))
      .unique();

    const currentBalance = existing?.balance ?? 0;
    const newBalance = currentBalance + amount;

    if (existing) {
      await ctx.db.patch(existing._id, { balance: newBalance });
    } else {
      await ctx.db.insert("userCredits", { userId, balance: newBalance });
    }

    await ctx.db.insert("creditTransactions", {
      userId,
      amount,
      reason,
      balanceAfter: newBalance,
      metadata,
    });

    return newBalance;
  },
});

export const useCredits = mutation({
  args: {
    userId: v.id("users"),
    amount: v.number(),
    reason: v.string(),
    metadata: v.optional(v.any()),
  },
  handler: async (ctx, { userId, amount, reason, metadata }) => {
    if (amount <= 0) {
      throw new ConvexError("Invalid deduction amount: must be positive");
    }

    await requireOwnership(ctx, userId);
    const existing = await ctx.db
      .query("userCredits")
      .withIndex("by_user", (q) => q.eq("userId", userId))
      .unique();

    const currentBalance = existing?.balance ?? 0;
    if (currentBalance < amount) {
      throw new ConvexError("insufficient_credits");
    }

    const newBalance = currentBalance - amount;

    // Non-negative guard: this should be guaranteed by the check above, but
    // we assert here to prevent any rounding or logic error from causing overdraft.
    if (newBalance < 0) {
      throw new ConvexError("insufficient_credits");
    }

    if (existing) {
      await ctx.db.patch(existing._id, { balance: newBalance });
    }

    await ctx.db.insert("creditTransactions", {
      userId,
      amount: -amount,
      reason,
      balanceAfter: newBalance,
      metadata,
    });

    return true;
  },
});

export const getTransactions = query({
  args: { userId: v.id("users"), limit: v.optional(v.number()) },
  handler: async (ctx, { userId, limit }) => {
    await requireOwnership(ctx, userId);
    // W3.2c — always-bounded; the prior else-branch was unbounded.
    return ctx.db
      .query("creditTransactions")
      .withIndex("by_user", (q) => q.eq("userId", userId))
      .order("desc")
      .take(limit ?? 200);
  },
});

// ─── Subscriptions ────────────────────────────────────────────────────────

export const getSubscription = query({
  args: { userId: v.id("users") },
  handler: async (ctx, { userId }) => {
    await requireOwnership(ctx, userId);
    return ctx.db
      .query("subscriptions")
      .withIndex("by_user", (q) => q.eq("userId", userId))
      .unique();
  },
});

// MC-1 (v20.6.2): upsertSubscription is webhook-only. Migrated mutation →
// internalMutation. Verified zero frontend callers via grep on
// api.billing.upsertSubscription.
export const upsertSubscription = internalMutation({
  args: {
    userId: v.id("users"),
    stripeSubscriptionId: v.string(),
    stripeCustomerId: v.string(),
    status: v.string(),
    plan: v.string(),
    interval: v.string(),
    currentPeriodStart: v.number(),
    currentPeriodEnd: v.number(),
    cancelAtPeriodEnd: v.boolean(),
  },
  handler: async (ctx, args) => {
    await requireOwnership(ctx, args.userId);
    const existing = await ctx.db
      .query("subscriptions")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .unique();

    if (existing) {
      await ctx.db.patch(existing._id, args);
      return existing._id;
    }
    return await ctx.db.insert("subscriptions", args);
  },
});

export const updateSubscriptionStatus = internalMutation({
  args: {
    stripeSubscriptionId: v.string(),
    status: v.string(),
    cancelAtPeriodEnd: v.optional(v.boolean()),
    currentPeriodStart: v.optional(v.number()),
    currentPeriodEnd: v.optional(v.number()),
  },
  handler: async (ctx, { stripeSubscriptionId, ...updates }) => {
    const sub = await ctx.db
      .query("subscriptions")
      .withIndex("by_stripe_sub", (q) =>
        q.eq("stripeSubscriptionId", stripeSubscriptionId)
      )
      .unique();

    if (sub) {
      await ctx.db.patch(sub._id, {
        status: updates.status,
        ...(updates.cancelAtPeriodEnd !== undefined && {
          cancelAtPeriodEnd: updates.cancelAtPeriodEnd,
        }),
        ...(updates.currentPeriodStart !== undefined && {
          currentPeriodStart: updates.currentPeriodStart,
        }),
        ...(updates.currentPeriodEnd !== undefined && {
          currentPeriodEnd: updates.currentPeriodEnd,
        }),
      });
    }
  },
});

export const cancelSubscriptionByStripeId = internalMutation({
  args: { stripeSubscriptionId: v.string() },
  handler: async (ctx, { stripeSubscriptionId }) => {
    const sub = await ctx.db
      .query("subscriptions")
      .withIndex("by_stripe_sub", (q) =>
        q.eq("stripeSubscriptionId", stripeSubscriptionId)
      )
      .unique();
    if (sub) {
      await ctx.db.patch(sub._id, { status: "canceled" });
    }
  },
});

// ─── Stripe Customers ─────────────────────────────────────────────────────

export const getOrCreateStripeCustomer = mutation({
  args: {
    userId: v.id("users"),
    stripeCustomerId: v.string(),
  },
  handler: async (ctx, { userId, stripeCustomerId }) => {
    await requireOwnership(ctx, userId);
    const existing = await ctx.db
      .query("stripeCustomers")
      .withIndex("by_user", (q) => q.eq("userId", userId))
      .unique();

    if (existing) return existing.stripeCustomerId;

    await ctx.db.insert("stripeCustomers", { userId, stripeCustomerId });
    return stripeCustomerId;
  },
});

export const getStripeCustomerId = query({
  args: { userId: v.id("users") },
  handler: async (ctx, { userId }) => {
    await requireOwnership(ctx, userId);
    const row = await ctx.db
      .query("stripeCustomers")
      .withIndex("by_user", (q) => q.eq("userId", userId))
      .unique();
    return row?.stripeCustomerId ?? null;
  },
});

export const getUserByStripeCustomerId = query({
  args: { stripeCustomerId: v.string() },
  handler: async (ctx, { stripeCustomerId }) => {
    await requireAuth(ctx);
    const row = await ctx.db
      .query("stripeCustomers")
      .withIndex("by_stripe_customer", (q) =>
        q.eq("stripeCustomerId", stripeCustomerId)
      )
      .unique();
    if (!row) return null;
    return ctx.db.get(row.userId);
  },
});
