import { mutation, query } from "./_generated/server";
import { v, ConvexError } from "convex/values";
import { requireAuth, requireProjectOwnership } from "./authHelpers";

// ============================================================================
// QUERIES
// ============================================================================

export const listByBuild = query({
  args: { buildId: v.id("builds") },
  handler: async (ctx, args) => {
    // W3.1 — agent status is project-scoped. Fetch the build to obtain its
    // projectId, then enforce ownership of that project.
    const build = await ctx.db.get(args.buildId);
    if (!build) throw new ConvexError("Build not found");
    await requireProjectOwnership(ctx, build.projectId);
    // W3.2c — per-build is typically ~10 agents; defensive cap.
    return await ctx.db
      .query("agentStatus")
      .withIndex("by_build", (q) => q.eq("buildId", args.buildId))
      .take(50);
  },
});

export const getByBuildAgent = query({
  args: {
    buildId: v.id("builds"),
    agentName: v.string(),
  },
  handler: async (ctx, args) => {
    // W3.1 — same ownership gate as listByBuild.
    const build = await ctx.db.get(args.buildId);
    if (!build) throw new ConvexError("Build not found");
    await requireProjectOwnership(ctx, build.projectId);
    return await ctx.db
      .query("agentStatus")
      .withIndex("by_build_agent", (q) =>
        q.eq("buildId", args.buildId).eq("agentName", args.agentName)
      )
      .first();
  },
});

// ============================================================================
// MUTATIONS (called by backend via HTTP API / ConvexWriteBuffer)
// ============================================================================

export const upsert = mutation({
  args: {
    buildId: v.id("builds"),
    agentName: v.string(),
    status: v.union(
      v.literal("idle"),
      v.literal("running"),
      v.literal("completed"),
      v.literal("failed"),
      v.literal("skipped")
    ),
    model: v.optional(v.string()),
    progressPercent: v.optional(v.number()),
    progressMessage: v.optional(v.string()),
    cost: v.optional(v.number()),
    tokens: v.optional(v.number()),
    duration: v.optional(v.number()),
    filesCreated: v.optional(v.array(v.string())),
    errorMessage: v.optional(v.string()),
    startedAt: v.optional(v.number()),
    completedAt: v.optional(v.number()),
    vos3Metadata: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    await requireAuth(ctx);
    const existing = await ctx.db
      .query("agentStatus")
      .withIndex("by_build_agent", (q) =>
        q.eq("buildId", args.buildId).eq("agentName", args.agentName)
      )
      .first();

    if (existing) {
      const updates: Record<string, unknown> = { status: args.status };
      if (args.model !== undefined) updates.model = args.model;
      if (args.progressPercent !== undefined)
        updates.progressPercent = args.progressPercent;
      if (args.progressMessage !== undefined)
        updates.progressMessage = args.progressMessage;
      if (args.cost !== undefined) updates.cost = args.cost;
      if (args.tokens !== undefined) updates.tokens = args.tokens;
      if (args.duration !== undefined) updates.duration = args.duration;
      if (args.filesCreated !== undefined) updates.filesCreated = args.filesCreated;
      if (args.errorMessage !== undefined) updates.errorMessage = args.errorMessage;
      if (args.startedAt !== undefined) updates.startedAt = args.startedAt;
      if (args.completedAt !== undefined) updates.completedAt = args.completedAt;
      if (args.vos3Metadata !== undefined) updates.vos3Metadata = args.vos3Metadata;
      await ctx.db.patch(existing._id, updates);
      return existing._id;
    }

    return await ctx.db.insert("agentStatus", {
      buildId: args.buildId,
      agentName: args.agentName,
      status: args.status,
      model: args.model,
      progressPercent: args.progressPercent,
      progressMessage: args.progressMessage,
      cost: args.cost,
      tokens: args.tokens,
      duration: args.duration,
      filesCreated: args.filesCreated,
      errorMessage: args.errorMessage,
      startedAt: args.startedAt,
      completedAt: args.completedAt,
      vos3Metadata: args.vos3Metadata,
    });
  },
});

export const remove = mutation({
  args: { id: v.id("agentStatus") },
  handler: async (ctx, args) => {
    await requireAuth(ctx);
    await ctx.db.delete(args.id);
  },
});
