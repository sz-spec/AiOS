import { v } from "convex/values";
import { paginationOptsValidator } from "convex/server";
import { mutation, query } from "./_generated/server";
import { requireAuth, requireMatchingUser } from "./authHelpers";

/**
 * Chat session persistence — save, load, list, delete.
 * Messages are stored in the separate chatSessionMessages table.
 */

export const save = mutation({
  args: {
    userId: v.string(),
    sessionId: v.string(),
    messages: v.array(
      v.object({
        role: v.string(),
        content: v.string(),
        timestamp: v.number(),
        metadata: v.optional(v.any()),
      })
    ),
  },
  handler: async (ctx, args) => {
    await requireMatchingUser(ctx, args.userId);
    const existing = await ctx.db
      .query("chatSessions")
      .withIndex("by_user_session", (q) =>
        q.eq("userId", args.userId).eq("sessionId", args.sessionId)
      )
      .first();

    let sessionDocId;

    if (existing) {
      await ctx.db.patch(existing._id, {
        messageCount: args.messages.length,
        updatedAt: Date.now(),
      });
      sessionDocId = existing._id;

      // W3.2c — cleanup cap; re-saving a session with >500 prior messages
      // would force the caller into a retry loop, which is intentional.
      const oldMessages = await ctx.db
        .query("chatSessionMessages")
        .withIndex("by_session", (q) => q.eq("sessionId", args.sessionId))
        .take(500);
      for (const msg of oldMessages) {
        await ctx.db.delete(msg._id);
      }
    } else {
      sessionDocId = await ctx.db.insert("chatSessions", {
        userId: args.userId,
        sessionId: args.sessionId,
        messageCount: args.messages.length,
        createdAt: Date.now(),
        updatedAt: Date.now(),
      });
    }

    // Insert messages into chatSessionMessages table
    for (const msg of args.messages) {
      await ctx.db.insert("chatSessionMessages", {
        sessionId: args.sessionId,
        role: msg.role,
        content: msg.content,
        timestamp: msg.timestamp,
        metadata: msg.metadata,
      });
    }

    return sessionDocId;
  },
});

export const load = query({
  args: { sessionId: v.string() },
  handler: async (ctx, args) => {
    const session = await ctx.db
      .query("chatSessions")
      .withIndex("by_session", (q) => q.eq("sessionId", args.sessionId))
      .first();
    if (!session) return null;
    // W3.1 — only the session owner can load its message history.
    await requireMatchingUser(ctx, session.userId);

    // W3.2c — per-session message history; defensive cap.
    const messages = await ctx.db
      .query("chatSessionMessages")
      .withIndex("by_session_ts", (q) => q.eq("sessionId", args.sessionId))
      .take(500);

    return { ...session, messages };
  },
});

export const list = query({
  args: {
    userId: v.string(),
    paginationOpts: paginationOptsValidator,
  },
  handler: async (ctx, args) => {
    // Phase v17: Verify caller owns this userId (was requireAuth only)
    await requireMatchingUser(ctx, args.userId);
    // W3.2c-2 — cursor-paginated chat history sidebar.
    return await ctx.db
      .query("chatSessions")
      .withIndex("by_user", (q) => q.eq("userId", args.userId))
      .order("desc")
      .paginate(args.paginationOpts);
  },
});

export const remove = mutation({
  args: { sessionId: v.string() },
  handler: async (ctx, args) => {
    await requireAuth(ctx);
    const session = await ctx.db
      .query("chatSessions")
      .withIndex("by_session", (q) => q.eq("sessionId", args.sessionId))
      .first();
    if (session) {
      await requireMatchingUser(ctx, session.userId);

      // W3.2c — cleanup cap; large sessions can be drained by re-invocation.
      const messages = await ctx.db
        .query("chatSessionMessages")
        .withIndex("by_session", (q) => q.eq("sessionId", args.sessionId))
        .take(500);
      for (const msg of messages) {
        await ctx.db.delete(msg._id);
      }

      await ctx.db.delete(session._id);
      return true;
    }
    return false;
  },
});
