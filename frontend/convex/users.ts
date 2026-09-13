import { mutation, query } from "./_generated/server";
import { v, ConvexError } from "convex/values";
import { requireAuth, requireMatchingUser, requireOwnership } from "./authHelpers";

// Sync a Clerk user into Convex (called from webhook or on first login)
export const syncFromClerk = mutation({
  args: {
    clerkId: v.string(),
    email: v.string(),
    fullName: v.optional(v.string()),
    avatarUrl: v.optional(v.string()),
    metadata: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    // syncFromClerk is called from webhooks with the backend deploy key,
    // so we require auth but don't restrict to matching user
    await requireAuth(ctx);
    const existing = await ctx.db
      .query("users")
      .withIndex("by_clerk_id", (q) => q.eq("clerkId", args.clerkId))
      .unique();

    if (existing) {
      await ctx.db.patch(existing._id, {
        email: args.email,
        fullName: args.fullName,
        avatarUrl: args.avatarUrl,
        metadata: args.metadata,
      });
      return existing._id;
    }

    return await ctx.db.insert("users", {
      clerkId: args.clerkId,
      email: args.email,
      fullName: args.fullName,
      avatarUrl: args.avatarUrl,
      metadata: args.metadata,
    });
  },
});

export const getByEmail = query({
  args: { email: v.string() },
  handler: async (ctx, { email }) => {
    await requireAuth(ctx);
    return ctx.db
      .query("users")
      .withIndex("by_email", (q) => q.eq("email", email))
      .unique();
  },
});

/**
 * Public user lookup by Clerk ID — used for @-mentions, presence
 * listings, and any UI that resolves a user reference into a display
 * card.
 *
 * W4.3 — strict-projection refactor. The pre-W4.3 handler returned the
 * entire `users` row, which leaks:
 *   - `email`     — PII, never exposed across users
 *   - `metadata`  — admin/internal flags (e.g. `deleted: true`, role
 *                   markers, billing references attached by webhooks)
 *
 * Post-W4.3 contract:
 *   - **Owner** (identity.subject matches clerkId) gets the full
 *     document so they can fetch their own profile.
 *   - **Anyone else** gets a hardcoded safe projection containing only
 *     mention/presence fields: clerkId, fullName (as displayName),
 *     avatarUrl, and lastSignInAt (for active presence state).
 *
 * `email` and `metadata` are NEVER returned to non-owners. The
 * projection is built from a literal-keyed object so adding a new
 * sensitive column to the users table can't accidentally leak.
 */
export const getByClerkId = query({
  args: { clerkId: v.string() },
  handler: async (ctx, { clerkId }) => {
    const identity = await requireAuth(ctx);
    const user = await ctx.db
      .query("users")
      .withIndex("by_clerk_id", (q) => q.eq("clerkId", clerkId))
      .unique();
    if (!user) return null;

    // Owner gets the full row.
    if (identity.subject === clerkId) return user;

    // Public projection — hardcoded allow-list, no spread.
    return {
      _id: user._id,
      _creationTime: user._creationTime,
      clerkId: user.clerkId,
      displayName: user.fullName ?? null,
      avatarUrl: user.avatarUrl ?? null,
      lastSignInAt: user.lastSignInAt ?? null,
    };
  },
});

export const getById = query({
  args: { id: v.id("users") },
  handler: async (ctx, { id }) => {
    await requireAuth(ctx);
    return ctx.db.get(id);
  },
});

export const update = mutation({
  args: {
    id: v.id("users"),
    email: v.optional(v.string()),
    fullName: v.optional(v.string()),
    avatarUrl: v.optional(v.string()),
    lastSignInAt: v.optional(v.number()),
    metadata: v.optional(v.any()),
  },
  handler: async (ctx, { id, ...updates }) => {
    await requireOwnership(ctx, id);
    await ctx.db.patch(id, updates);
  },
});

export const recordSignIn = mutation({
  args: { clerkId: v.string() },
  handler: async (ctx, { clerkId }) => {
    const identity = await requireAuth(ctx);
    if (identity.subject !== clerkId) {
      throw new ConvexError("Forbidden: clerkId mismatch");
    }
    const user = await ctx.db
      .query("users")
      .withIndex("by_clerk_id", (q) => q.eq("clerkId", clerkId))
      .unique();
    if (user) {
      await ctx.db.patch(user._id, { lastSignInAt: Date.now() });
    }
  },
});

export const softDelete = mutation({
  args: { clerkId: v.string() },
  handler: async (ctx, { clerkId }) => {
    await requireAuth(ctx);
    const user = await ctx.db
      .query("users")
      .withIndex("by_clerk_id", (q) => q.eq("clerkId", clerkId))
      .unique();
    if (user) {
      await ctx.db.patch(user._id, {
        metadata: { ...(user.metadata ?? {}), deleted: true, deletedAt: Date.now() },
      });
    }
  },
});
