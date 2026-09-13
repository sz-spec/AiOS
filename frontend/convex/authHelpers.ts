/**
 * Shared Authentication Helpers for Convex Functions
 * ===================================================
 * Provides auth guard utilities for mutations and queries.
 *
 * Usage:
 *   const identity = await requireAuth(ctx);
 *   // identity.subject is the Clerk user ID (e.g. "user_xxx")
 */
import { ConvexError } from "convex/values";
import { QueryCtx, MutationCtx } from "./_generated/server";
import { Id } from "./_generated/dataModel";

type AuthCtx = QueryCtx | MutationCtx;

/**
 * Require authenticated identity. Throws ConvexError if not authenticated.
 * Returns the UserIdentity from Clerk.
 */
export async function requireAuth(ctx: AuthCtx) {
  const identity = await ctx.auth.getUserIdentity();
  if (!identity) {
    throw new ConvexError("Unauthorized");
  }
  return identity;
}

/**
 * Verify the caller's Clerk subject matches the given clerkId.
 * For mutations that accept a clerkId/userId arg, call this to prevent impersonation.
 */
export async function requireMatchingUser(ctx: AuthCtx, clerkId: string) {
  const identity = await requireAuth(ctx);
  if (identity.subject !== clerkId) {
    throw new ConvexError("Forbidden: user identity mismatch");
  }
  return identity;
}

/**
 * Verify the caller owns the given Convex user record.
 * Looks up the user by Doc ID, compares its clerkId to identity.subject.
 * Use for mutations that accept a userId: v.id("users") argument.
 */
export async function requireOwnership(ctx: AuthCtx, userId: Id<"users">) {
  const identity = await requireAuth(ctx);
  const user = await ctx.db.get(userId);
  if (!user) {
    throw new ConvexError("User not found");
  }
  if (user.clerkId !== identity.subject) {
    throw new ConvexError("Forbidden: user identity mismatch");
  }
  return { identity, user };
}

/**
 * Verify the caller owns the project (project.ownerId resolves to caller's clerkId).
 * Use for queries/mutations that accept a projectId: v.id("projects") argument.
 */
export async function requireProjectOwnership(ctx: AuthCtx, projectId: Id<"projects">) {
  const identity = await requireAuth(ctx);
  const project = await ctx.db.get(projectId);
  if (!project) {
    throw new ConvexError("Project not found");
  }
  const user = await ctx.db
    .query("users")
    .withIndex("by_clerk_id", (q) => q.eq("clerkId", identity.subject))
    .unique();
  if (!user) {
    throw new ConvexError("User not found");
  }
  if (project.ownerId !== user._id) {
    throw new ConvexError("Forbidden: not the project owner");
  }
  return { identity, project, user };
}

/**
 * Verify the caller is a member of the specified organization.
 * Looks up the members table for a matching (orgId, userId) entry
 * where userId is resolved from the caller's Clerk identity.
 */
export async function requireOrgMember(ctx: AuthCtx, organizationId: Id<"organizations">) {
  const identity = await requireAuth(ctx);
  // Resolve caller's Convex user ID from their Clerk subject
  const user = await ctx.db
    .query("users")
    .withIndex("by_clerk_id", (q) => q.eq("clerkId", identity.subject))
    .unique();
  if (!user) {
    throw new ConvexError("User not found");
  }

  const membership = await ctx.db
    .query("members")
    .withIndex("by_org_and_user", (q) =>
      q.eq("organizationId", organizationId).eq("userId", user._id)
    )
    .unique();

  if (!membership) {
    throw new ConvexError("Forbidden: not a member of this organization");
  }

  return { identity, user, membership };
}
