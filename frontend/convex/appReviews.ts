import { v } from "convex/values";
import { paginationOptsValidator } from "convex/server";
import { mutation, query } from "./_generated/server";
import { requireAuth, requireMatchingUser } from "./authHelpers";

export const create = mutation({
  args: {
    appId: v.id("apps"),
    userId: v.string(),
    rating: v.number(),
    title: v.optional(v.string()),
    body: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    await requireMatchingUser(ctx, args.userId);
    if (args.rating < 1 || args.rating > 5) throw new Error("Rating must be 1-5");

    const reviewId = await ctx.db.insert("appReviews", {
      ...args,
      helpfulVotes: 0,
    });

    // Incremental average: (avg * count + new) / (count + 1)
    const app = await ctx.db.get(args.appId);
    if (app) {
      const count = (app.ratingCount ?? 0);
      const sum = (app.ratingSum ?? 0);
      const newSum = sum + args.rating;
      const newCount = count + 1;
      await ctx.db.patch(args.appId, {
        avgRating: Math.round((newSum / newCount) * 10) / 10,
        ratingSum: newSum,
        ratingCount: newCount,
      });
    }

    return reviewId;
  },
});

export const list = query({
  args: {
    appId: v.id("apps"),
    paginationOpts: paginationOptsValidator,
  },
  handler: async (ctx, args) => {
    // W3.2c-2 — cursor-paginated. Reviews per app can grow unbounded.
    // Marked PUBLIC in idor_fuzz allow-list (Amazon-style storefront).
    return await ctx.db
      .query("appReviews")
      .withIndex("by_app", (q) => q.eq("appId", args.appId))
      .order("desc")
      .paginate(args.paginationOpts);
  },
});

export const averageRating = query({
  args: { appId: v.id("apps") },
  handler: async (ctx, args) => {
    const app = await ctx.db.get(args.appId);
    if (!app) return { average: 0, count: 0 };
    const count = app.ratingCount ?? 0;
    const sum = app.ratingSum ?? 0;
    return {
      average: count > 0 ? Math.round((sum / count) * 10) / 10 : 0,
      count,
    };
  },
});

export const voteHelpful = mutation({
  args: { reviewId: v.id("appReviews") },
  handler: async (ctx, args) => {
    await requireAuth(ctx);
    const review = await ctx.db.get(args.reviewId);
    if (review) {
      await ctx.db.patch(args.reviewId, { helpfulVotes: review.helpfulVotes + 1 });
    }
  },
});
