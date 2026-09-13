import { mutation, query } from "./_generated/server";
import { v } from "convex/values";
import { paginationOptsValidator } from "convex/server";
import { requireAuth, requireOrgMember } from "./authHelpers";

// Reusable validator aliases matching schema.ts types
const vFlexObj = v.record(v.string(), v.any());

// ─── Entities ─────────────────────────────────────────────────────────────

export const createEntity = mutation({
  args: {
    organizationId: v.id("organizations"),
    name: v.string(),
    slug: v.string(),
    description: v.optional(v.string()),
    fields: v.array(vFlexObj),
    metadata: v.optional(vFlexObj),
  },
  handler: async (ctx, args) => {
    await requireOrgMember(ctx, args.organizationId);
    return ctx.db.insert("entities", args);
  },
});

export const getEntity = query({
  args: { id: v.id("entities") },
  handler: async (ctx, { id }) => {
    const entity = await ctx.db.get(id);
    if (!entity) return null;
    await requireOrgMember(ctx, entity.organizationId);
    return entity;
  },
});

export const listEntities = query({
  args: {
    organizationId: v.id("organizations"),
    paginationOpts: paginationOptsValidator,
  },
  handler: async (ctx, { organizationId, paginationOpts }) => {
    // W3.1 — gate by org membership (matches mutations in this file).
    await requireOrgMember(ctx, organizationId);
    // W3.2c-2 — cursor-paginated business-OS entity list.
    return ctx.db
      .query("entities")
      .withIndex("by_org", (q) => q.eq("organizationId", organizationId))
      .paginate(paginationOpts);
  },
});

export const updateEntity = mutation({
  args: {
    id: v.id("entities"),
    name: v.optional(v.string()),
    description: v.optional(v.string()),
    fields: v.optional(v.array(vFlexObj)),
    metadata: v.optional(vFlexObj),
  },
  handler: async (ctx, { id, ...updates }) => {
    // Phase v17: Verify caller is member of the entity's org
    const entity = await ctx.db.get(id);
    if (!entity) throw new Error("Entity not found");
    await requireOrgMember(ctx, entity.organizationId);
    return ctx.db.patch(id, updates);
  },
});

export const deleteEntity = mutation({
  args: { id: v.id("entities") },
  handler: async (ctx, { id }) => {
    // Phase v17: Verify caller is member of the entity's org
    const entity = await ctx.db.get(id);
    if (!entity) throw new Error("Entity not found");
    await requireOrgMember(ctx, entity.organizationId);
    return ctx.db.delete(id);
  },
});

// ─── Records ──────────────────────────────────────────────────────────────

export const createRecord = mutation({
  args: {
    entityId: v.id("entities"),
    organizationId: v.id("organizations"),
    data: vFlexObj,
    createdBy: v.optional(v.id("users")),
  },
  handler: async (ctx, args) => {
    await requireOrgMember(ctx, args.organizationId);
    return ctx.db.insert("records", args);
  },
});

export const getRecord = query({
  args: { id: v.id("records") },
  handler: async (ctx, { id }) => {
    const record = await ctx.db.get(id);
    if (!record) return null;
    await requireOrgMember(ctx, record.organizationId);
    return record;
  },
});

export const listRecords = query({
  args: {
    entityId: v.id("entities"),
    paginationOpts: paginationOptsValidator,
  },
  handler: async (ctx, { entityId, paginationOpts }) => {
    await requireAuth(ctx);
    // W3.2c-2 — cursor-paginated. Records-per-entity is the highest
    // blast-radius unbounded set in vcore.
    return ctx.db
      .query("records")
      .withIndex("by_entity", (q) => q.eq("entityId", entityId))
      .paginate(paginationOpts);
  },
});

export const updateRecord = mutation({
  args: { id: v.id("records"), data: vFlexObj },
  handler: async (ctx, { id, data }) => {
    // Phase v17: Verify caller is member of the record's org
    const record = await ctx.db.get(id);
    if (!record) throw new Error("Record not found");
    await requireOrgMember(ctx, record.organizationId);
    return ctx.db.patch(id, { data });
  },
});

export const deleteRecord = mutation({
  args: { id: v.id("records") },
  handler: async (ctx, { id }) => {
    // Phase v17: Verify caller is member of the record's org
    const record = await ctx.db.get(id);
    if (!record) throw new Error("Record not found");
    await requireOrgMember(ctx, record.organizationId);
    return ctx.db.delete(id);
  },
});

// ─── Workflows ────────────────────────────────────────────────────────────

export const createWorkflow = mutation({
  args: {
    organizationId: v.id("organizations"),
    name: v.string(),
    description: v.optional(v.string()),
    trigger: vFlexObj,
    actions: v.array(vFlexObj),
    isActive: v.boolean(),
    metadata: v.optional(vFlexObj),
  },
  handler: async (ctx, args) => {
    await requireOrgMember(ctx, args.organizationId);
    return ctx.db.insert("workflows", args);
  },
});

export const getWorkflow = query({
  args: { id: v.id("workflows") },
  handler: async (ctx, { id }) => {
    const workflow = await ctx.db.get(id);
    if (!workflow) return null;
    await requireOrgMember(ctx, workflow.organizationId);
    return workflow;
  },
});

export const listWorkflows = query({
  args: { organizationId: v.id("organizations") },
  handler: async (ctx, { organizationId }) => {
    // W3.1 — gate by org membership.
    await requireOrgMember(ctx, organizationId);
    // W3.2c — per-org workflows; defensive cap.
    return ctx.db
      .query("workflows")
      .withIndex("by_org", (q) => q.eq("organizationId", organizationId))
      .take(200);
  },
});

export const updateWorkflow = mutation({
  args: {
    id: v.id("workflows"),
    name: v.optional(v.string()),
    isActive: v.optional(v.boolean()),
    actions: v.optional(v.array(vFlexObj)),
    trigger: v.optional(vFlexObj),
    metadata: v.optional(vFlexObj),
  },
  handler: async (ctx, { id, ...updates }) => {
    // Phase v17: Verify caller is member of the workflow's org
    const workflow = await ctx.db.get(id);
    if (!workflow) throw new Error("Workflow not found");
    await requireOrgMember(ctx, workflow.organizationId);
    return ctx.db.patch(id, updates);
  },
});

export const deleteWorkflow = mutation({
  args: { id: v.id("workflows") },
  handler: async (ctx, { id }) => {
    // Phase v17: Verify caller is member of the workflow's org
    const workflow = await ctx.db.get(id);
    if (!workflow) throw new Error("Workflow not found");
    await requireOrgMember(ctx, workflow.organizationId);
    return ctx.db.delete(id);
  },
});

// ─── Workflow Executions ──────────────────────────────────────────────────

export const createExecution = mutation({
  args: {
    workflowId: v.id("workflows"),
    organizationId: v.id("organizations"),
    triggeredBy: v.optional(v.id("users")),
  },
  handler: async (ctx, args) => {
    await requireOrgMember(ctx, args.organizationId);
    return ctx.db.insert("workflowExecutions", {
      ...args,
      status: "pending",
      startedAt: Date.now(),
    });
  },
});

export const updateExecution = mutation({
  args: {
    id: v.id("workflowExecutions"),
    // W4.4 — enum-constrained per schema.ts:94 workflow lifecycle.
    // The orchestrator's transition table assumes one of these four
    // states; an unknown string would stall executions in an
    // unrecognized state with no automatic recovery path.
    status: v.union(
      v.literal("pending"),
      v.literal("running"),
      v.literal("completed"),
      v.literal("failed"),
    ),
    completedAt: v.optional(v.number()),
    result: v.optional(vFlexObj),
    error: v.optional(v.string()),
  },
  handler: async (ctx, { id, ...updates }) => {
    // Phase v17: Verify caller is member of the execution's org
    const execution = await ctx.db.get(id);
    if (!execution) throw new Error("Execution not found");
    await requireOrgMember(ctx, execution.organizationId);
    return ctx.db.patch(id, updates);
  },
});

export const listExecutions = query({
  args: { workflowId: v.id("workflows"), limit: v.optional(v.number()) },
  handler: async (ctx, { workflowId, limit }) => {
    await requireAuth(ctx);
    // W3.2c — always-bounded; the prior else-branch was unbounded.
    return ctx.db
      .query("workflowExecutions")
      .withIndex("by_workflow", (q) => q.eq("workflowId", workflowId))
      .order("desc")
      .take(limit ?? 100);
  },
});

// ─── API Keys ─────────────────────────────────────────────────────────────

export const createApiKey = mutation({
  args: {
    organizationId: v.id("organizations"),
    name: v.string(),
    keyHash: v.string(),
    prefix: v.string(),
    createdBy: v.id("users"),
    lastUsedAt: v.optional(v.number()),
    expiresAt: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    await requireOrgMember(ctx, args.organizationId);
    return ctx.db.insert("apiKeys", { ...args, isActive: true });
  },
});

export const getApiKey = query({
  args: { id: v.id("apiKeys") },
  handler: async (ctx, { id }) => {
    await requireAuth(ctx);
    return ctx.db.get(id);
  },
});

export const getApiKeyByHash = query({
  args: { keyHash: v.string() },
  handler: async (ctx, { keyHash }) => {
    await requireAuth(ctx);
    return ctx.db
      .query("apiKeys")
      .withIndex("by_hash", (q) => q.eq("keyHash", keyHash))
      .unique();
  },
});

export const updateApiKey = mutation({
  args: {
    id: v.id("apiKeys"),
    name: v.optional(v.string()),
    isActive: v.optional(v.boolean()),
    lastUsedAt: v.optional(v.number()),
    expiresAt: v.optional(v.number()),
  },
  handler: async (ctx, { id, ...updates }) => {
    const apiKey = await ctx.db.get(id);
    if (!apiKey) throw new Error("API key not found");
    await requireOrgMember(ctx, apiKey.organizationId);
    return ctx.db.patch(id, updates);
  },
});

export const deleteApiKey = mutation({
  args: { id: v.id("apiKeys") },
  handler: async (ctx, { id }) => {
    const apiKey = await ctx.db.get(id);
    if (!apiKey) throw new Error("API key not found");
    await requireOrgMember(ctx, apiKey.organizationId);
    return ctx.db.delete(id);
  },
});

export const listApiKeys = query({
  args: { organizationId: v.id("organizations") },
  handler: async (ctx, { organizationId }) => {
    // W3.1 CRITICAL — API keys are secrets. Org-membership gate is mandatory.
    await requireOrgMember(ctx, organizationId);
    // W3.2c — per-org API keys; defensive cap.
    return ctx.db
      .query("apiKeys")
      .withIndex("by_org", (q) => q.eq("organizationId", organizationId))
      .filter((q) => q.eq(q.field("isActive"), true))
      .take(100);
  },
});

// ─── Audit Log ────────────────────────────────────────────────────────────

export const addAuditEntry = mutation({
  args: {
    organizationId: v.optional(v.id("organizations")),
    userId: v.optional(v.id("users")),
    action: v.string(),
    resourceType: v.string(),
    resourceId: v.optional(v.string()),
    metadata: v.optional(vFlexObj),
    ipAddress: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    if (args.organizationId) {
      await requireOrgMember(ctx, args.organizationId);
    } else {
      await requireAuth(ctx);
    }
    return ctx.db.insert("auditLog", args);
  },
});

export const listAuditLog = query({
  args: {
    organizationId: v.id("organizations"),
    limit: v.optional(v.number()),
  },
  handler: async (ctx, { organizationId, limit }) => {
    // W3.1 CRITICAL — audit-log entries are compliance evidence. Org gate.
    await requireOrgMember(ctx, organizationId);
    // W3.2c — always-bounded; the prior else-branch was unbounded.
    return ctx.db
      .query("auditLog")
      .withIndex("by_org", (q) => q.eq("organizationId", organizationId))
      .order("desc")
      .take(limit ?? 200);
  },
});
