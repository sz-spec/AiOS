/**
 * Expert-in-the-Loop Convex Queries & Mutations
 * ================================================
 * IE-2 Phase 2.0 — SOS expert help request persistence.
 */

import { v } from "convex/values";
import { paginationOptsValidator } from "convex/server";
import { query, mutation } from "./_generated/server";
import { requireAuth, requireOwnership, requireProjectOwnership } from "./authHelpers";

// ─── Queries ─────────────────────────────────────────────────

export const getByProject = query({
  args: { projectId: v.id("projects") },
  handler: async (ctx, { projectId }) => {
    await requireAuth(ctx);
    await requireProjectOwnership(ctx, projectId);
    // W3.2c — per-project requests; defensive cap.
    return await ctx.db
      .query("expertRequests")
      .withIndex("by_project", (q) => q.eq("projectId", projectId))
      .order("desc")
      .take(100);
  },
});

export const getByUser = query({
  args: { userId: v.id("users") },
  handler: async (ctx, { userId }) => {
    // Phase v17: Verify caller owns this userId
    await requireOwnership(ctx, userId);
    // W3.2c — per-user requests; defensive cap.
    return await ctx.db
      .query("expertRequests")
      .withIndex("by_user", (q) => q.eq("userId", userId))
      .order("desc")
      .take(100);
  },
});

export const listPending = query({
  args: { paginationOpts: paginationOptsValidator },
  handler: async (ctx, { paginationOpts }) => {
    await requireAuth(ctx);
    // W3.2c-2 — cross-user pending queue; cursor-paginated.
    return await ctx.db
      .query("expertRequests")
      .withIndex("by_status", (q) => q.eq("status", "pending"))
      .order("desc")
      .paginate(paginationOpts);
  },
});

export const getById = query({
  args: { id: v.id("expertRequests") },
  handler: async (ctx, { id }) => {
    await requireAuth(ctx);
    return await ctx.db.get(id);
  },
});

// ─── Mutations ───────────────────────────────────────────────

export const create = mutation({
  args: {
    userId: v.id("users"),
    projectId: v.id("projects"),
    buildId: v.optional(v.string()),
    context: v.optional(v.any()),
    userDescription: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    // Phase v17: Verify caller owns this userId (was requireAuth only)
    await requireOwnership(ctx, args.userId);
    return await ctx.db.insert("expertRequests", {
      ...args,
      status: "pending",
    });
  },
});

export const claim = mutation({
  args: {
    id: v.id("expertRequests"),
    claimedBy: v.string(),
  },
  handler: async (ctx, { id, claimedBy }) => {
    await requireAuth(ctx);
    const req = await ctx.db.get(id);
    if (!req || req.status !== "pending") {
      throw new Error("Request not found or already claimed");
    }
    await ctx.db.patch(id, {
      status: "claimed",
      claimedBy,
      claimedAt: Date.now(),
    });
    return id;
  },
});

export const resolve = mutation({
  args: {
    id: v.id("expertRequests"),
    resolution: v.any(),
  },
  handler: async (ctx, { id, resolution }) => {
    await requireAuth(ctx);
    const req = await ctx.db.get(id);
    if (!req || (req.status !== "pending" && req.status !== "claimed")) {
      throw new Error("Request not found or cannot be resolved");
    }
    await ctx.db.patch(id, {
      status: "resolved",
      resolution,
      resolvedAt: Date.now(),
    });
    return id;
  },
});
