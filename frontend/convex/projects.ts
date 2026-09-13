import { mutation, query } from "./_generated/server";
import { v, ConvexError } from "convex/values";
import { paginationOptsValidator } from "convex/server";
import { Id } from "./_generated/dataModel";
import { requireAuth, requireOwnership, requireProjectOwnership, requireMatchingUser } from "./authHelpers";

export const create = mutation({
  args: {
    name: v.string(),
    description: v.optional(v.string()),
    ownerId: v.id("users"),
    organizationId: v.optional(v.id("organizations")),
    metadata: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    await requireOwnership(ctx, args.ownerId);
    return ctx.db.insert("projects", { ...args, isArchived: false });
  },
});

export const getById = query({
  args: { id: v.id("projects") },
  handler: async (ctx, { id }) => {
    const { project } = await requireProjectOwnership(ctx, id);
    return project;
  },
});

export const listByUser = query({
  args: {
    userId: v.id("users"),
    paginationOpts: paginationOptsValidator,
  },
  handler: async (ctx, { userId, paginationOpts }) => {
    await requireOwnership(ctx, userId);
    // W3.2c-2 — cursor-paginated. Result: { page, isDone, continueCursor }.
    return ctx.db
      .query("projects")
      .withIndex("by_owner", (q) => q.eq("ownerId", userId))
      .filter((q) => q.eq(q.field("isArchived"), false))
      .paginate(paginationOpts);
  },
});

export const update = mutation({
  args: {
    id: v.id("projects"),
    name: v.optional(v.string()),
    description: v.optional(v.string()),
    isArchived: v.optional(v.boolean()),
    metadata: v.optional(v.any()),
  },
  handler: async (ctx, { id, ...updates }) => {
    await requireProjectOwnership(ctx, id);
    return ctx.db.patch(id, updates);
  },
});

// ─── Files ────────────────────────────────────────────────────────────────

export const upsertFile = mutation({
  args: {
    projectId: v.id("projects"),
    path: v.string(),
    content: v.string(),
    language: v.optional(v.string()),
    updatedBy: v.optional(v.id("users")),
  },
  handler: async (ctx, args) => {
    await requireProjectOwnership(ctx, args.projectId);
    const existing = await ctx.db
      .query("projectFiles")
      .withIndex("by_project_path", (q) =>
        q.eq("projectId", args.projectId).eq("path", args.path)
      )
      .unique();

    if (existing) {
      await ctx.db.patch(existing._id, {
        content: args.content,
        language: args.language,
        updatedBy: args.updatedBy,
      });
      return existing._id;
    }
    return ctx.db.insert("projectFiles", args);
  },
});

export const getFiles = query({
  args: {
    projectId: v.id("projects"),
    paginationOpts: paginationOptsValidator,
  },
  handler: async (ctx, { projectId, paginationOpts }) => {
    await requireProjectOwnership(ctx, projectId);
    // W3.2c-2 — cursor-paginated. File trees can grow unbounded.
    return ctx.db
      .query("projectFiles")
      .withIndex("by_project", (q) => q.eq("projectId", projectId))
      .paginate(paginationOpts);
  },
});

export const deleteFile = mutation({
  args: { id: v.id("projectFiles") },
  handler: async (ctx, { id }) => {
    const file = await ctx.db.get(id);
    if (!file) throw new ConvexError("File not found");
    await requireProjectOwnership(ctx, file.projectId);
    return ctx.db.delete(id);
  },
});

// ─── Chat ─────────────────────────────────────────────────────────────────

export const addChatMessage = mutation({
  args: {
    projectId: v.id("projects"),
    userId: v.id("users"),
    // W4.4 — enum-constrained per schema.ts:259 chat contract.
    // Allowing arbitrary strings would let an attacker insert messages
    // claiming to be "system" or "assistant" (e.g. for prompt
    // injection in a downstream LLM session that re-reads the history).
    role: v.union(
      v.literal("user"),
      v.literal("assistant"),
      v.literal("system"),
    ),
    content: v.string(),
    metadata: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    await requireProjectOwnership(ctx, args.projectId);
    await requireOwnership(ctx, args.userId);
    return ctx.db.insert("chatMessages", args);
  },
});

export const getChatMessages = query({
  args: { projectId: v.id("projects"), limit: v.optional(v.number()) },
  handler: async (ctx, { projectId, limit }) => {
    await requireProjectOwnership(ctx, projectId);
    // W3.2c — always-bounded; the prior `q.collect()` else-branch could
    // OOM on chat-heavy projects.
    return ctx.db
      .query("chatMessages")
      .withIndex("by_project", (q) => q.eq("projectId", projectId))
      .order("asc")
      .take(limit ?? 200);
  },
});

// ─── Project Memory ───────────────────────────────────────────────────────

export const createMemory = mutation({
  args: {
    projectId: v.optional(v.string()),
    memoryType: v.string(),
    title: v.string(),
    content: v.string(),
    tags: v.array(v.string()),
    metadata: v.optional(v.any()),
    userId: v.optional(v.id("users")),
  },
  handler: async (ctx, args) => {
    if (args.userId) {
      await requireOwnership(ctx, args.userId);
    } else {
      await requireAuth(ctx);
    }
    return ctx.db.insert("projectMemory", args);
  },
});

export const getMemory = query({
  args: { id: v.id("projectMemory") },
  handler: async (ctx, { id }) => {
    const memory = await ctx.db.get(id);
    if (!memory) throw new ConvexError("Memory not found");
    if (memory.projectId) {
      await requireProjectOwnership(ctx, memory.projectId as Id<"projects">);
    } else {
      await requireAuth(ctx);
    }
    return memory;
  },
});

export const updateMemory = mutation({
  args: {
    id: v.id("projectMemory"),
    title: v.optional(v.string()),
    content: v.optional(v.string()),
    tags: v.optional(v.array(v.string())),
    metadata: v.optional(v.any()),
  },
  handler: async (ctx, { id, ...updates }) => {
    const memory = await ctx.db.get(id);
    if (!memory) throw new ConvexError("Memory not found");
    if (memory.projectId) {
      await requireProjectOwnership(ctx, memory.projectId as Id<"projects">);
    } else {
      await requireAuth(ctx);
    }
    return ctx.db.patch(id, updates);
  },
});

export const deleteMemory = mutation({
  args: { id: v.id("projectMemory") },
  handler: async (ctx, { id }) => {
    const memory = await ctx.db.get(id);
    if (!memory) throw new ConvexError("Memory not found");
    if (memory.projectId) {
      await requireProjectOwnership(ctx, memory.projectId as Id<"projects">);
    } else {
      await requireAuth(ctx);
    }
    return ctx.db.delete(id);
  },
});

export const listMemory = query({
  args: { projectId: v.string(), limit: v.optional(v.number()) },
  handler: async (ctx, { projectId, limit }) => {
    await requireProjectOwnership(ctx, projectId as Id<"projects">);
    // W3.2c — always-bounded; the prior else-branch was unbounded.
    return ctx.db
      .query("projectMemory")
      .withIndex("by_project", (q) => q.eq("projectId", projectId))
      .order("desc")
      .take(limit ?? 200);
  },
});

export const listMemoryByType = query({
  args: { memoryType: v.string(), limit: v.optional(v.number()) },
  handler: async (ctx, { memoryType, limit }) => {
    const identity = await requireAuth(ctx);
    // Resolve caller's Convex user
    const user = await ctx.db
      .query("users")
      .withIndex("by_clerk_id", (q: any) => q.eq("clerkId", identity.subject))
      .unique();
    if (!user) throw new ConvexError("User not found");
    // W3.2c — defensive cap on owned-projects fetch. Users with >500
    // projects would hit this; that's an unusual signal worth surfacing.
    const projects = await ctx.db
      .query("projects")
      .withIndex("by_owner", (q) => q.eq("ownerId", user._id))
      .take(500);
    const ownedProjectIds = new Set(projects.map((p) => p._id as string));
    // W3.2c — defensive cap. Memories of a given type across the platform
    // can grow unbounded. Better: a follow-up should restructure this to
    // query per-project rather than collect-then-filter.
    const all = await ctx.db
      .query("projectMemory")
      .withIndex("by_type", (q) => q.eq("memoryType", memoryType))
      .order("desc")
      .take(1000);
    const filtered = all.filter(
      (m) => !m.projectId || ownedProjectIds.has(m.projectId)
    );
    return limit ? filtered.slice(0, limit) : filtered;
  },
});

// ─── Prompt History ───────────────────────────────────────────────────────

// Summary projection for list views — omits MB-scale response/generatedCode/generatedFiles fields
export const listSummary = query({
  args: { userId: v.string(), limit: v.optional(v.number()) },
  handler: async (ctx, { userId, limit }) => {
    // W3.1 — prompt history can include sensitive draft content. Caller
    // must own the userId they query.
    await requireMatchingUser(ctx, userId);
    const rows = await ctx.db
      .query("promptHistory")
      .withIndex("by_user", (q) => q.eq("userId", userId))
      .order("desc")
      .take(limit ?? 100);
    // Return only summary fields, omitting response/generatedCode/generatedFiles
    // to avoid fetching MB-scale data in list views
    return rows.map((r) => ({
      _id: r._id,
      _creationTime: r._creationTime,
      promptId: r.promptId,
      userId: r.userId,
      projectId: r.projectId,
      sessionId: r.sessionId,
      prompt: r.prompt,
      promptType: r.promptType,
      status: r.status,
      model: r.model,
      promptTokens: r.promptTokens,
      completionTokens: r.completionTokens,
      totalTokens: r.totalTokens,
      durationMs: r.durationMs,
      error: r.error,
      tags: r.tags,
      isFavorite: r.isFavorite,
      createdAt: r.createdAt,
    }));
  },
});
