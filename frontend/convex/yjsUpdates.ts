import { mutation, query } from "./_generated/server";
import { v, ConvexError } from "convex/values";
import { requireAuth, requireProjectOwnership } from "./authHelpers";

// ─── Document Management ──────────────────────────────────────────────────

/** Get or create a Yjs document entry for a project file. */
export const getOrCreateDocument = mutation({
  args: {
    projectId: v.id("projects"),
    filePath: v.string(),
  },
  handler: async (ctx, { projectId, filePath }) => {
    await requireAuth(ctx);
    await requireProjectOwnership(ctx, projectId);
    const existing = await ctx.db
      .query("yjsDocuments")
      .withIndex("by_project_file", (q) =>
        q.eq("projectId", projectId).eq("filePath", filePath)
      )
      .unique();

    if (existing) return existing._id;

    const now = Date.now();
    return ctx.db.insert("yjsDocuments", {
      projectId,
      filePath,
      createdAt: now,
      updatedAt: now,
    });
  },
});

/** Get a document by project + file path. */
export const getDocument = query({
  args: {
    projectId: v.id("projects"),
    filePath: v.string(),
  },
  handler: async (ctx, { projectId, filePath }) => {
    // W3.1 — collab document content must not leak across projects.
    await requireProjectOwnership(ctx, projectId);
    return ctx.db
      .query("yjsDocuments")
      .withIndex("by_project_file", (q) =>
        q.eq("projectId", projectId).eq("filePath", filePath)
      )
      .unique();
  },
});

/** List all collaborative documents for a project. */
export const listDocuments = query({
  args: { projectId: v.id("projects") },
  handler: async (ctx, { projectId }) => {
    // W3.1 — same ownership gate as getDocument.
    await requireProjectOwnership(ctx, projectId);
    // W3.2c — index-bounded by project; defensive cap.
    return ctx.db
      .query("yjsDocuments")
      .withIndex("by_project", (q) => q.eq("projectId", projectId))
      .take(200);
  },
});

// ─── CRDT Updates ─────────────────────────────────────────────────────────

/**
 * Push a Yjs CRDT update delta.
 */
export const pushUpdate = mutation({
  args: {
    documentId: v.id("yjsDocuments"),
    update: v.bytes(),
    clientId: v.string(),
  },
  handler: async (ctx, { documentId, update, clientId }) => {
    await requireAuth(ctx);
    const doc = await ctx.db.get(documentId);
    if (!doc) throw new ConvexError("Document not found");
    await requireProjectOwnership(ctx, doc.projectId);
    // Get current max sequence number for this document
    const latest = await ctx.db
      .query("yjsUpdates")
      .withIndex("by_document_seq", (q) => q.eq("documentId", documentId))
      .order("desc")
      .first();

    const seq = (latest?.seq ?? 0) + 1;

    const updateId = await ctx.db.insert("yjsUpdates", {
      documentId,
      update,
      clientId,
      seq,
      createdAt: Date.now(),
    });

    // Touch the document's updatedAt
    await ctx.db.patch(documentId, { updatedAt: Date.now() });

    return { updateId, seq };
  },
});

/**
 * Fetch all updates for a document since a given sequence number.
 */
export const getUpdatesSince = query({
  args: {
    documentId: v.id("yjsDocuments"),
    sinceSeq: v.number(),
  },
  handler: async (ctx, { documentId, sinceSeq }) => {
    await requireAuth(ctx);
    // W3.2c — CRDT correctness needs all deltas after sinceSeq; defensive
    // cap forces aggressive compaction if reached.
    return ctx.db
      .query("yjsUpdates")
      .withIndex("by_document_seq", (q) => q.eq("documentId", documentId).gt("seq", sinceSeq))
      .order("asc")
      .take(1000);
  },
});

/** Get latest sequence number for a document (for sync protocol). */
export const getLatestSeq = query({
  args: { documentId: v.id("yjsDocuments") },
  handler: async (ctx, { documentId }) => {
    await requireAuth(ctx);
    const latest = await ctx.db
      .query("yjsUpdates")
      .withIndex("by_document_seq", (q) => q.eq("documentId", documentId))
      .order("desc")
      .first();
    return latest?.seq ?? 0;
  },
});

/**
 * Update the state vector for a document.
 */
export const updateStateVector = mutation({
  args: {
    documentId: v.id("yjsDocuments"),
    stateVector: v.bytes(),
  },
  handler: async (ctx, { documentId, stateVector }) => {
    await requireAuth(ctx);
    const doc = await ctx.db.get(documentId);
    if (!doc) throw new ConvexError("Document not found");
    await requireProjectOwnership(ctx, doc.projectId);
    return ctx.db.patch(documentId, { stateVector, updatedAt: Date.now() });
  },
});

/**
 * Compact old updates by replacing them with a merged snapshot.
 */
export const compactUpdates = mutation({
  args: {
    documentId: v.id("yjsDocuments"),
    compactUpToSeq: v.number(),
    mergedUpdate: v.bytes(),
  },
  handler: async (ctx, { documentId, compactUpToSeq, mergedUpdate }) => {
    await requireAuth(ctx);
    const doc = await ctx.db.get(documentId);
    if (!doc) throw new ConvexError("Document not found");
    await requireProjectOwnership(ctx, doc.projectId);
    // W3.2c — cleanup loop with defensive cap. Large compactions should
    // run in batches; the caller can re-invoke compactUpdates to drain.
    const oldUpdates = await ctx.db
      .query("yjsUpdates")
      .withIndex("by_document_seq", (q) => q.eq("documentId", documentId).lte("seq", compactUpToSeq))
      .take(1000);

    for (const update of oldUpdates) {
      await ctx.db.delete(update._id);
    }

    // Insert merged snapshot as seq=0 (always first when fetching)
    await ctx.db.insert("yjsUpdates", {
      documentId,
      update: mergedUpdate,
      clientId: "__compaction__",
      seq: 0,
      createdAt: Date.now(),
    });

    return { deletedCount: oldUpdates.length };
  },
});
