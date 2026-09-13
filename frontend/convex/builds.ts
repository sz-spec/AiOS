import { mutation, query } from "./_generated/server";
import { v, ConvexError } from "convex/values";
import { paginationOptsValidator } from "convex/server";
import { requireAuth, requireOwnership, requireProjectOwnership } from "./authHelpers";

// ============================================================================
// QUERIES
// ============================================================================

export const getById = query({
  args: { id: v.id("builds") },
  handler: async (ctx, args) => {
    // W3.1 — fetch first, then verify caller owns the project that owns
    // the build. Returning null for a missing build matches the prior
    // contract while requireProjectOwnership stops cross-tenant reads.
    const build = await ctx.db.get(args.id);
    if (!build) return null;
    await requireProjectOwnership(ctx, build.projectId);
    return build;
  },
});

export const listByProject = query({
  args: {
    projectId: v.id("projects"),
    paginationOpts: paginationOptsValidator,
  },
  handler: async (ctx, args) => {
    // W3.1 — gate by project ownership before reading the build list.
    await requireProjectOwnership(ctx, args.projectId);
    // W3.2c-2 — cursor-paginated. Build history accumulates over project life.
    return await ctx.db
      .query("builds")
      .withIndex("by_project", (q) => q.eq("projectId", args.projectId))
      .order("desc")
      .paginate(args.paginationOpts);
  },
});

export const listByUser = query({
  args: {
    userId: v.id("users"),
    paginationOpts: paginationOptsValidator,
  },
  handler: async (ctx, args) => {
    // W3.1 — only the user themselves can list their builds.
    await requireOwnership(ctx, args.userId);
    // W3.2c-2 — cursor-paginated.
    return await ctx.db
      .query("builds")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .order("desc")
      .paginate(args.paginationOpts);
  },
});

export const getLatestByProject = query({
  args: { projectId: v.id("projects") },
  handler: async (ctx, args) => {
    // W3.1 — same ownership gate as listByProject.
    await requireProjectOwnership(ctx, args.projectId);
    return await ctx.db
      .query("builds")
      .withIndex("by_project", (q) => q.eq("projectId", args.projectId))
      .order("desc")
      .first();
  },
});

// ============================================================================
// MUTATIONS (called by backend via HTTP API)
// ============================================================================

export const create = mutation({
  args: {
    projectId: v.id("projects"),
    userId: v.id("users"),
    requirements: v.string(),
    status: v.optional(
      v.union(
        v.literal("queued"),
        v.literal("running"),
        v.literal("completed"),
        v.literal("failed"),
        v.literal("cancelled")
      )
    ),
    createdAt: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    await requireOwnership(ctx, args.userId);
    const now = Date.now();
    return await ctx.db.insert("builds", {
      projectId: args.projectId,
      userId: args.userId,
      requirements: args.requirements,
      status: args.status ?? "queued",
      createdAt: args.createdAt ?? now,
    });
  },
});

export const updateStatus = mutation({
  args: {
    id: v.id("builds"),
    status: v.union(
      v.literal("queued"),
      v.literal("running"),
      v.literal("completed"),
      v.literal("failed"),
      v.literal("cancelled")
    ),
    errorMessage: v.optional(v.string()),
    completedAt: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    await requireAuth(ctx);
    const build = await ctx.db.get(args.id);
    if (!build) throw new ConvexError("Build not found");
    await requireProjectOwnership(ctx, build.projectId);
    const updates: Record<string, unknown> = { status: args.status };
    if (args.errorMessage !== undefined) updates.errorMessage = args.errorMessage;
    if (
      args.status === "completed" ||
      args.status === "failed" ||
      args.status === "cancelled"
    ) {
      updates.completedAt = args.completedAt ?? Date.now();
    }
    await ctx.db.patch(args.id, updates);
    return await ctx.db.get(args.id);
  },
});

export const updateCost = mutation({
  args: {
    id: v.id("builds"),
    totalCost: v.number(),
    totalTokens: v.number(),
    totalDuration: v.number(),
    costBreakdown: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    await requireAuth(ctx);
    const build = await ctx.db.get(args.id);
    if (!build) throw new ConvexError("Build not found");
    await requireProjectOwnership(ctx, build.projectId);
    await ctx.db.patch(args.id, {
      totalCost: args.totalCost,
      totalTokens: args.totalTokens,
      totalDuration: args.totalDuration,
      costBreakdown: args.costBreakdown,
    });
  },
});

export const updateScores = mutation({
  args: {
    id: v.id("builds"),
    securityScore: v.optional(v.number()),
    completenessScore: v.optional(v.number()),
    filesGenerated: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    await requireAuth(ctx);
    const build = await ctx.db.get(args.id);
    if (!build) throw new ConvexError("Build not found");
    await requireProjectOwnership(ctx, build.projectId);
    const updates: Record<string, unknown> = {};
    if (args.securityScore !== undefined) updates.securityScore = args.securityScore;
    if (args.completenessScore !== undefined)
      updates.completenessScore = args.completenessScore;
    if (args.filesGenerated !== undefined) updates.filesGenerated = args.filesGenerated;
    await ctx.db.patch(args.id, updates);
  },
});

export const remove = mutation({
  args: { id: v.id("builds") },
  handler: async (ctx, args) => {
    await requireAuth(ctx);
    const build = await ctx.db.get(args.id);
    if (!build) throw new ConvexError("Build not found");
    await requireProjectOwnership(ctx, build.projectId);
    // W3.2c — cleanup cap; per-build is bounded but defends against a
    // pathological build with many agent-status rows.
    const agentStatuses = await ctx.db
      .query("agentStatus")
      .withIndex("by_build", (q) => q.eq("buildId", args.id))
      .take(100);
    for (const status of agentStatuses) {
      await ctx.db.delete(status._id);
    }
    // W3.2c — cleanup cap as above.
    const snapshots = await ctx.db
      .query("contextSnapshots")
      .withIndex("by_build", (q) => q.eq("buildId", args.id))
      .take(100);
    for (const snapshot of snapshots) {
      await ctx.db.delete(snapshot._id);
    }
    await ctx.db.delete(args.id);
  },
});
