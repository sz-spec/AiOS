import { mutation, query } from "./_generated/server";
import { v } from "convex/values";
import { requireAuth, requireMatchingUser, requireProjectOwnership } from "./authHelpers";

// Stale threshold: presence entries older than 30s are considered offline
const STALE_THRESHOLD_MS = 30_000;

// ─── Heartbeat / Cursor Update ────────────────────────────────────────────

/**
 * Upsert presence for a user in a project.
 * Called every ~5s as heartbeat, and on cursor/selection changes.
 */
export const heartbeat = mutation({
  args: {
    projectId: v.id("projects"),
    userId: v.string(),
    displayName: v.string(),
    avatarUrl: v.optional(v.string()),
    color: v.string(),
    filePath: v.optional(v.string()),
    cursorLine: v.optional(v.number()),
    cursorColumn: v.optional(v.number()),
    selectionStartLine: v.optional(v.number()),
    selectionStartColumn: v.optional(v.number()),
    selectionEndLine: v.optional(v.number()),
    selectionEndColumn: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    await requireMatchingUser(ctx, args.userId);
    const existing = await ctx.db
      .query("presence")
      .withIndex("by_project_user", (q) =>
        q.eq("projectId", args.projectId).eq("userId", args.userId)
      )
      .unique();

    const now = Date.now();

    if (existing) {
      await ctx.db.patch(existing._id, {
        displayName: args.displayName,
        avatarUrl: args.avatarUrl,
        color: args.color,
        filePath: args.filePath,
        cursorLine: args.cursorLine,
        cursorColumn: args.cursorColumn,
        selectionStartLine: args.selectionStartLine,
        selectionStartColumn: args.selectionStartColumn,
        selectionEndLine: args.selectionEndLine,
        selectionEndColumn: args.selectionEndColumn,
        lastHeartbeat: now,
        isOnline: true,
      });
      return existing._id;
    }

    return ctx.db.insert("presence", {
      projectId: args.projectId,
      userId: args.userId,
      displayName: args.displayName,
      avatarUrl: args.avatarUrl,
      color: args.color,
      filePath: args.filePath,
      cursorLine: args.cursorLine,
      cursorColumn: args.cursorColumn,
      selectionStartLine: args.selectionStartLine,
      selectionStartColumn: args.selectionStartColumn,
      selectionEndLine: args.selectionEndLine,
      selectionEndColumn: args.selectionEndColumn,
      lastHeartbeat: now,
      isOnline: true,
    });
  },
});

/**
 * Mark a user as offline (called on disconnect / page unload).
 */
export const disconnect = mutation({
  args: {
    projectId: v.id("projects"),
    userId: v.string(),
  },
  handler: async (ctx, { projectId, userId }) => {
    await requireMatchingUser(ctx, userId);
    const existing = await ctx.db
      .query("presence")
      .withIndex("by_project_user", (q) =>
        q.eq("projectId", projectId).eq("userId", userId)
      )
      .unique();

    if (existing) {
      await ctx.db.patch(existing._id, { isOnline: false });
    }
  },
});

// ─── Queries ──────────────────────────────────────────────────────────────

/**
 * List all active (online and not stale) users in a project.
 */
export const getActiveUsers = query({
  args: { projectId: v.id("projects") },
  handler: async (ctx, { projectId }) => {
    // W3.1 — presence reveals who is collaborating on a project. Ownership gate.
    await requireProjectOwnership(ctx, projectId);
    // W3.2c — index-bounded by project + filtered to online users only.
    const all = await ctx.db
      .query("presence")
      .withIndex("by_project", (q) => q.eq("projectId", projectId))
      .filter((q) => q.eq(q.field("isOnline"), true))
      .take(200);

    const cutoff = Date.now() - STALE_THRESHOLD_MS;
    return all.filter((p) => p.lastHeartbeat >= cutoff);
  },
});

/**
 * Get all cursors for a specific file within a project.
 */
export const getCursorsForFile = query({
  args: {
    projectId: v.id("projects"),
    filePath: v.string(),
    excludeUserId: v.optional(v.string()),
  },
  handler: async (ctx, { projectId, filePath, excludeUserId }) => {
    // W3.1 — same ownership gate as getActiveUsers.
    await requireProjectOwnership(ctx, projectId);
    // W3.2c — index-bounded by project, narrowed by file+online filter.
    const all = await ctx.db
      .query("presence")
      .withIndex("by_project", (q) => q.eq("projectId", projectId))
      .filter((q) =>
        q.and(
          q.eq(q.field("isOnline"), true),
          q.eq(q.field("filePath"), filePath)
        )
      )
      .take(200);

    const cutoff = Date.now() - STALE_THRESHOLD_MS;
    return all.filter(
      (p) => p.lastHeartbeat >= cutoff && p.userId !== excludeUserId
    );
  },
});

/**
 * Clean up stale presence entries (garbage collection).
 */
export const cleanupStale = mutation({
  args: { projectId: v.id("projects") },
  handler: async (ctx, { projectId }) => {
    await requireAuth(ctx);
    // W3.2c — cleanup loop with defensive cap; re-invoke to drain larger sets.
    const all = await ctx.db
      .query("presence")
      .withIndex("by_project", (q) => q.eq("projectId", projectId))
      .take(500);

    const cutoff = Date.now() - STALE_THRESHOLD_MS;
    let cleaned = 0;

    for (const entry of all) {
      if (entry.lastHeartbeat < cutoff && entry.isOnline) {
        await ctx.db.patch(entry._id, { isOnline: false });
        cleaned++;
      }
    }

    return { cleaned };
  },
});
