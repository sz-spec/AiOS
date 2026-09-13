import { mutation, query } from "./_generated/server";
import { v, ConvexError } from "convex/values";
import { requireAuth, requireMatchingUser, requireOrgMember, requireOwnership } from "./authHelpers";

export const create = mutation({
  args: {
    name: v.string(),
    slug: v.string(),
    ownerId: v.string(),
    logoUrl: v.optional(v.string()),
    metadata: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    await requireMatchingUser(ctx, args.ownerId);
    const owner = await ctx.db.query("users")
      .withIndex("by_clerk_id", (q) => q.eq("clerkId", args.ownerId))
      .unique();
    if (!owner) throw new ConvexError("User not found");
    const organizationId = await ctx.db.insert("organizations", {
      name: args.name,
      slug: args.slug,
      ownerId: owner._id,
      logoUrl: args.logoUrl,
      metadata: args.metadata,
    });
    // Creation and membership are one transaction: the owner can immediately
    // access the new organization through the same guards as later requests.
    await ctx.db.insert("members", {
      organizationId, userId: owner._id, role: "owner", joinedAt: Date.now(),
    });
    return organizationId;
  },
});

export const getById = query({
  args: { id: v.id("organizations") },
  handler: async (ctx, { id }) => {
    await requireOrgMember(ctx, id);
    return ctx.db.get(id);
  },
});

export const getBySlug = query({
  args: { slug: v.string() },
  handler: async (ctx, { slug }) => {
    await requireAuth(ctx);
    const organization = await ctx.db
      .query("organizations")
      .withIndex("by_slug", (q) => q.eq("slug", slug))
      .unique();
    if (organization) await requireOrgMember(ctx, organization._id);
    return organization;
  },
});

export const listByUser = query({
  args: { userId: v.id("users") },
  handler: async (ctx, { userId }) => {
    // Phase v17: Verify caller owns this userId
    await requireOwnership(ctx, userId);
    // W3.2c — per-user memberships; defensive cap.
    const memberships = await ctx.db
      .query("members")
      .withIndex("by_user", (q) => q.eq("userId", userId))
      .take(50);

    return Promise.all(memberships.map((m) => ctx.db.get(m.organizationId)));
  },
});

export const update = mutation({
  args: {
    id: v.id("organizations"),
    name: v.optional(v.string()),
    logoUrl: v.optional(v.string()),
    metadata: v.optional(v.any()),
  },
  handler: async (ctx, { id, ...updates }) => {
    // Phase v17: Verify caller is member of this org
    await requireOrgMember(ctx, id);
    await ctx.db.patch(id, updates);
  },
});

export const addMember = mutation({
  args: {
    organizationId: v.id("organizations"),
    userId: v.id("users"),
    // W4.4 — role is enum-constrained at the type level. Privilege
    // escalation surface: passing a string the RBAC layer treats as
    // higher-privilege (`owner`, `admin`) without org-side checks
    // would be game over. Restricting to the canonical 4-role set
    // forces explicit schema-side review for any new role.
    role: v.union(
      v.literal("owner"),
      v.literal("admin"),
      v.literal("editor"),
      v.literal("viewer"),
    ),
  },
  handler: async (ctx, { organizationId, userId, role }) => {
    await requireOrgMember(ctx, organizationId);
    const existing = await ctx.db
      .query("members")
      .withIndex("by_org_and_user", (q) =>
        q.eq("organizationId", organizationId).eq("userId", userId)
      )
      .unique();

    if (existing) {
      await ctx.db.patch(existing._id, { role });
      return existing._id;
    }

    return await ctx.db.insert("members", {
      organizationId,
      userId,
      role,
      joinedAt: Date.now(),
    });
  },
});

export const removeMember = mutation({
  args: {
    organizationId: v.id("organizations"),
    userId: v.id("users"),
  },
  handler: async (ctx, { organizationId, userId }) => {
    await requireOrgMember(ctx, organizationId);
    const member = await ctx.db
      .query("members")
      .withIndex("by_org_and_user", (q) =>
        q.eq("organizationId", organizationId).eq("userId", userId)
      )
      .unique();

    if (member) {
      await ctx.db.delete(member._id);
    }
  },
});

export const listMembers = query({
  args: { organizationId: v.id("organizations") },
  handler: async (ctx, { organizationId }) => {
    // W3.1 — member roster is org-private.
    await requireOrgMember(ctx, organizationId);
    // W3.2c — per-org members; defensive cap.
    return ctx.db
      .query("members")
      .withIndex("by_org", (q) => q.eq("organizationId", organizationId))
      .take(500);
  },
});
