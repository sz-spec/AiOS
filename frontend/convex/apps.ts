import { v, ConvexError } from "convex/values";
import { paginationOptsValidator } from "convex/server";
import { mutation, query } from "./_generated/server";
import { requireAuth, requireMatchingUser, requireOrgMember } from "./authHelpers";

export const publish = mutation({
  args: {
    developerId: v.string(),
    name: v.string(),
    slug: v.string(),
    description: v.string(),
    // W4.4 — `category` is intentionally free-form: marketplace
    // categories are dynamic and operator-defined. The fuzzer
    // allow-lists this via APPROVED_PLAIN_STRINGS.
    category: v.string(),
    // W4.4 — type-level enum closes the input-validation gap for the
    // canonical app types (see schema.ts:382). Adding a new type
    // requires editing this union + the schema in lockstep.
    type: v.union(
      v.literal("plugin"),
      v.literal("fullstack"),
      v.literal("kernel"),
    ),
    latestVersion: v.string(),
    icon: v.optional(v.string()),
    screenshots: v.array(v.string()),
    // W4.4 — billing pathways switch on this string; downstream
    // services assume one of three known values (see schema.ts:386).
    pricing: v.union(
      v.literal("free"),
      v.literal("paid"),
      v.literal("subscription"),
    ),
    price: v.number(),
    revenueSharePercent: v.number(),
    permissions: v.array(v.string()),
    manifest: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    await requireMatchingUser(ctx, args.developerId);
    // W4.4 — Convex has no built-in numeric-range validator, so range
    // checks live in the handler. revenueSharePercent feeds the payout
    // engine; values outside 0-100 would corrupt every downstream
    // calculation. price must be non-negative (no "give me money to
    // download" pricing).
    if (args.revenueSharePercent < 0 || args.revenueSharePercent > 100) {
      throw new ConvexError("revenueSharePercent must be between 0 and 100");
    }
    if (args.price < 0) {
      throw new ConvexError("price must be non-negative");
    }
    return await ctx.db.insert("apps", {
      ...args,
      status: "review",
      downloads: 0,
      avgRating: 0,
    });
  },
});

export const install = mutation({
  args: {
    appId: v.id("apps"),
    organizationId: v.id("organizations"),
    installedBy: v.string(),
    version: v.string(),
    grantedScopes: v.array(v.string()),
    config: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    await requireMatchingUser(ctx, args.installedBy);
    // Check not already installed
    const existing = await ctx.db
      .query("appInstallations")
      .withIndex("by_org_app", (q) => q.eq("organizationId", args.organizationId).eq("appId", args.appId))
      .first();
    if (existing) return existing._id;

    // Increment download count
    const app = await ctx.db.get(args.appId);
    if (app) {
      await ctx.db.patch(args.appId, { downloads: app.downloads + 1 });
    }

    return await ctx.db.insert("appInstallations", {
      ...args,
      enabled: true,
      installedAt: Date.now(),
    });
  },
});

export const uninstall = mutation({
  args: {
    appId: v.id("apps"),
    organizationId: v.id("organizations"),
  },
  handler: async (ctx, args) => {
    await requireAuth(ctx);
    await requireOrgMember(ctx, args.organizationId);
    const installation = await ctx.db
      .query("appInstallations")
      .withIndex("by_org_app", (q) => q.eq("organizationId", args.organizationId).eq("appId", args.appId))
      .first();
    if (installation) {
      await ctx.db.delete(installation._id);
      return true;
    }
    return false;
  },
});

export const get = query({
  args: { slug: v.string() },
  handler: async (ctx, args) => {
    return await ctx.db
      .query("apps")
      .withIndex("by_slug", (q) => q.eq("slug", args.slug))
      .first();
  },
});

export const list = query({
  args: {
    category: v.optional(v.string()),
    status: v.optional(v.string()),
    paginationOpts: paginationOptsValidator,
  },
  handler: async (ctx, args) => {
    // W3.2c-2 — cursor-paginated marketplace browse. Each branch returns
    // the same { page, isDone, continueCursor } shape so the caller can
    // treat the result uniformly.
    if (args.category) {
      // Index-time filter on category; status filter applied to the page.
      const result = await ctx.db
        .query("apps")
        .withIndex("by_category", (i) => i.eq("category", args.category!))
        .paginate(args.paginationOpts);
      if (args.status) {
        return {
          ...result,
          page: result.page.filter((a) => a.status === args.status),
        };
      }
      return result;
    }
    if (args.status) {
      return await ctx.db
        .query("apps")
        .withIndex("by_status", (i) => i.eq("status", args.status!))
        .paginate(args.paginationOpts);
    }
    return await ctx.db.query("apps").paginate(args.paginationOpts);
  },
});

export const search = query({
  args: { query: v.string() },
  handler: async (ctx, args) => {
    const q = args.query.trim();
    if (q.length === 0) return [];
    // Use search index for name matches
    const results = await ctx.db
      .query("apps")
      .withSearchIndex("search_apps", (s) => s.search("name", q).eq("status", "published"))
      .take(50);
    return results;
  },
});

export const getInstalled = query({
  args: { organizationId: v.id("organizations") },
  handler: async (ctx, args) => {
    // W3.1 — org-installed apps list is org-private.
    await requireOrgMember(ctx, args.organizationId);
    // W3.2c — index-bounded by org; defensive cap.
    return await ctx.db
      .query("appInstallations")
      .withIndex("by_org", (q) => q.eq("organizationId", args.organizationId))
      .take(200);
  },
});

export const updateStatus = mutation({
  args: {
    appId: v.id("apps"),
    status: v.union(
      v.literal("draft"),
      v.literal("review"),
      v.literal("published"),
      v.literal("building"),
      v.literal("running"),
      v.literal("stopped"),
      v.literal("error")
    ),
  },
  handler: async (ctx, args) => {
    const identity = await requireAuth(ctx);
    const app = await ctx.db.get(args.appId);
    if (!app) {
      throw new ConvexError("App not found");
    }
    const user = await ctx.db
      .query("users")
      .withIndex("by_clerk_id", (q) => q.eq("clerkId", identity.subject))
      .unique();
    if (!user || app.developerId !== user._id) {
      throw new ConvexError("Forbidden: not the app developer");
    }
    await ctx.db.patch(args.appId, { status: args.status });
  },
});
