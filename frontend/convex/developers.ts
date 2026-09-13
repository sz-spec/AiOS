import { v, ConvexError } from "convex/values";
import { mutation, query } from "./_generated/server";
import { requireAuth, requireMatchingUser } from "./authHelpers";

export const register = mutation({
  args: {
    userId: v.string(),
    displayName: v.string(),
    email: v.string(),
    website: v.optional(v.string()),
    bio: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    await requireMatchingUser(ctx, args.userId);
    const existing = await ctx.db
      .query("developers")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .first();
    if (existing) return existing._id;

    return await ctx.db.insert("developers", {
      ...args,
      stripeConnectId: undefined,
      verified: false,
      totalEarnings: 0,
      totalApps: 0,
    });
  },
});

/**
 * Public developer profile lookup — used by marketplace UIs and "about
 * the developer" pages.
 *
 * W4.3 — strict-projection refactor. The pre-W4.3 handler returned the
 * entire `developers` row, which leaks:
 *   - `email`           — PII, separate from any consent flow
 *   - `stripeConnectId` — financial routing ID, target for fraud pivots
 *   - `totalEarnings`   — competitive intelligence + financial PII
 *
 * Post-W4.3 contract:
 *   - **Owner** (identity.subject matches the developer's userId) gets
 *     the full document, since they're inspecting their own record.
 *   - **Anyone else** gets a hardcoded safe projection containing only
 *     marketplace-public fields: displayName, bio, website, verified,
 *     and totalApps.
 *
 * `email`, `stripeConnectId`, `totalEarnings`, and the internal
 * `userId` reference are NEVER returned to non-owners. The projection
 * is built from a literal-keyed object (not a spread + delete pattern)
 * so adding a new sensitive column to the schema can't accidentally
 * leak through the API.
 */
export const getProfile = query({
  args: { userId: v.string() },
  handler: async (ctx, args) => {
    const identity = await requireAuth(ctx);
    const dev = await ctx.db
      .query("developers")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .first();
    if (!dev) return null;

    // Owner sees their own full record (including the sensitive fields
    // they need to manage). The .userId column is the Clerk subject —
    // see backend/api/developer_routes.py:register.
    const isOwner = identity.subject === dev.userId;
    if (isOwner) return dev;

    // Public projection — hardcoded allow-list, no spread.
    return {
      _id: dev._id,
      _creationTime: dev._creationTime,
      displayName: dev.displayName,
      bio: dev.bio ?? null,
      website: dev.website ?? null,
      verified: dev.verified,
      totalApps: dev.totalApps,
    };
  },
});

export const getApps = query({
  args: { developerId: v.string() },
  handler: async (ctx, args) => {
    await requireAuth(ctx);
    // W3.2c — index-bounded by developer; defensive cap.
    return await ctx.db
      .query("apps")
      .withIndex("by_developer", (q) => q.eq("developerId", args.developerId))
      .take(200);
  },
});

export const getEarnings = query({
  args: { developerId: v.id("developers") },
  handler: async (ctx, args) => {
    // W3.1 — earnings are sensitive financial data. Verify the caller owns
    // the developer record before exposing the payout history.
    const developer = await ctx.db.get(args.developerId);
    if (!developer) {
      throw new ConvexError("Developer not found");
    }
    await requireMatchingUser(ctx, developer.userId);

    // W3.2c — aggregating reduce needs all payouts; defensive cap. A
    // developer with >1000 payouts should be paged via a separate query.
    const payouts = await ctx.db
      .query("developerPayouts")
      .withIndex("by_developer", (q) => q.eq("developerId", args.developerId))
      .take(1000);

    const total = payouts.reduce((sum, p) => sum + p.amount, 0);
    const pending = payouts
      .filter((p) => p.status === "pending")
      .reduce((sum, p) => sum + p.amount, 0);

    return { total, pending, payouts };
  },
});

export const updateStripeConnect = mutation({
  args: {
    developerId: v.id("developers"),
    stripeConnectId: v.string(),
  },
  handler: async (ctx, args) => {
    const identity = await requireAuth(ctx);
    const dev = await ctx.db.get(args.developerId);
    if (!dev) throw new ConvexError("Developer not found");
    // Verify caller is the developer
    const user = await ctx.db
      .query("users")
      .withIndex("by_clerk_id", (q: any) => q.eq("clerkId", identity.subject))
      .unique();
    if (!user || dev.userId !== user._id) {
      throw new ConvexError("Forbidden: not your developer profile");
    }
    await ctx.db.patch(args.developerId, { stripeConnectId: args.stripeConnectId });
  },
});
