import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  // -------------------------------------------------------------------------
  // Auth / Users
  // -------------------------------------------------------------------------
  users: defineTable({
    clerkId: v.string(),
    email: v.string(),
    fullName: v.optional(v.string()),
    avatarUrl: v.optional(v.string()),
    lastSignInAt: v.optional(v.number()),
    metadata: v.optional(v.record(v.string(), v.any())),
  })
    .index("by_clerk_id", ["clerkId"])
    .index("by_email", ["email"]),

  // -------------------------------------------------------------------------
  // Organizations
  // -------------------------------------------------------------------------
  organizations: defineTable({
    name: v.string(),
    slug: v.string(),
    logoUrl: v.optional(v.string()),
    ownerId: v.id("users"),
    metadata: v.optional(v.record(v.string(), v.any())),
  }).index("by_slug", ["slug"]),

  roles: defineTable({
    organizationId: v.id("organizations"),
    name: v.string(),
    permissions: v.array(v.string()),
  }).index("by_org", ["organizationId"]),

  members: defineTable({
    organizationId: v.id("organizations"),
    userId: v.id("users"),
    role: v.string(),
    joinedAt: v.number(),
  })
    .index("by_org", ["organizationId"])
    .index("by_user", ["userId"])
    .index("by_org_and_user", ["organizationId", "userId"]),

  invitations: defineTable({
    organizationId: v.id("organizations"),
    email: v.string(),
    role: v.string(),
    token: v.string(),
    invitedBy: v.id("users"),
    expiresAt: v.number(),
    acceptedAt: v.optional(v.number()),
  })
    .index("by_org", ["organizationId"])
    .index("by_token", ["token"]),

  // -------------------------------------------------------------------------
  // V-Core Business OS
  // -------------------------------------------------------------------------
  entities: defineTable({
    organizationId: v.id("organizations"),
    name: v.string(),
    slug: v.string(),
    description: v.optional(v.string()),
    fields: v.array(v.record(v.string(), v.any())),
    metadata: v.optional(v.record(v.string(), v.any())),
  })
    .index("by_org", ["organizationId"])
    .index("by_org_slug", ["organizationId", "slug"]),

  records: defineTable({
    entityId: v.id("entities"),
    organizationId: v.id("organizations"),
    data: v.record(v.string(), v.any()),
    createdBy: v.optional(v.id("users")),
  })
    .index("by_entity", ["entityId"])
    .index("by_org", ["organizationId"]),

  workflows: defineTable({
    organizationId: v.id("organizations"),
    name: v.string(),
    description: v.optional(v.string()),
    trigger: v.record(v.string(), v.any()),
    actions: v.array(v.record(v.string(), v.any())),
    isActive: v.boolean(),
    metadata: v.optional(v.record(v.string(), v.any())),
  }).index("by_org", ["organizationId"]),

  workflowExecutions: defineTable({
    workflowId: v.id("workflows"),
    organizationId: v.id("organizations"),
    status: v.string(), // pending | running | completed | failed
    triggeredBy: v.optional(v.id("users")),
    startedAt: v.number(),
    completedAt: v.optional(v.number()),
    result: v.optional(v.record(v.string(), v.any())),
    error: v.optional(v.string()),
  })
    .index("by_workflow", ["workflowId"])
    .index("by_org", ["organizationId"]),

  metrics: defineTable({
    organizationId: v.id("organizations"),
    name: v.string(),
    value: v.number(),
    unit: v.optional(v.string()),
    dimensions: v.optional(v.record(v.string(), v.any())),
    recordedAt: v.number(),
  })
    .index("by_org", ["organizationId"])
    .index("by_org_name", ["organizationId", "name"]),

  approvals: defineTable({
    organizationId: v.id("organizations"),
    requestedBy: v.id("users"),
    approvedBy: v.optional(v.id("users")),
    resourceType: v.string(),
    resourceId: v.string(),
    status: v.string(), // pending | approved | rejected
    notes: v.optional(v.string()),
    requestedAt: v.number(),
    resolvedAt: v.optional(v.number()),
  }).index("by_org", ["organizationId"]),

  alerts: defineTable({
    organizationId: v.id("organizations"),
    type: v.string(),
    severity: v.string(), // info | warning | error | critical
    message: v.string(),
    metadata: v.optional(v.record(v.string(), v.any())),
    resolvedAt: v.optional(v.number()),
    isRead: v.boolean(),
  }).index("by_org", ["organizationId"]),

  auditLog: defineTable({
    organizationId: v.optional(v.id("organizations")),
    userId: v.optional(v.id("users")),
    action: v.string(),
    resourceType: v.string(),
    resourceId: v.optional(v.string()),
    metadata: v.optional(v.record(v.string(), v.any())),
    ipAddress: v.optional(v.string()),
  })
    .index("by_org", ["organizationId"])
    .index("by_user", ["userId"]),

  // -------------------------------------------------------------------------
  // API Keys
  // -------------------------------------------------------------------------
  apiKeys: defineTable({
    organizationId: v.id("organizations"),
    name: v.string(),
    keyHash: v.string(),
    prefix: v.string(),
    createdBy: v.id("users"),
    lastUsedAt: v.optional(v.number()),
    expiresAt: v.optional(v.number()),
    isActive: v.boolean(),
  })
    .index("by_org", ["organizationId"])
    .index("by_hash", ["keyHash"]),

  // -------------------------------------------------------------------------
  // Billing
  // -------------------------------------------------------------------------
  subscriptions: defineTable({
    userId: v.id("users"),
    stripeSubscriptionId: v.string(),
    stripeCustomerId: v.string(),
    status: v.string(), // active | canceled | past_due | trialing | incomplete
    plan: v.string(), // free | pro | team
    interval: v.string(), // monthly | yearly
    currentPeriodStart: v.number(),
    currentPeriodEnd: v.number(),
    cancelAtPeriodEnd: v.boolean(),
  })
    .index("by_user", ["userId"])
    .index("by_stripe_sub", ["stripeSubscriptionId"]),

  stripeCustomers: defineTable({
    userId: v.id("users"),
    stripeCustomerId: v.string(),
  })
    .index("by_user", ["userId"])
    .index("by_stripe_customer", ["stripeCustomerId"]),

  userCredits: defineTable({
    userId: v.id("users"),
    balance: v.number(),
  }).index("by_user", ["userId"]),

  creditTransactions: defineTable({
    userId: v.id("users"),
    amount: v.number(), // positive = added, negative = used
    reason: v.string(),
    balanceAfter: v.number(),
    metadata: v.optional(v.record(v.string(), v.any())),
  }).index("by_user", ["userId"]),

  // -------------------------------------------------------------------------
  // Quota
  // -------------------------------------------------------------------------
  quotaUsage: defineTable({
    userId: v.id("users"),
    organizationId: v.optional(v.id("organizations")),
    resource: v.string(),
    used: v.number(),
    limit: v.number(),
    periodStart: v.number(),
    periodEnd: v.number(),
  })
    .index("by_user", ["userId"])
    .index("by_user_resource", ["userId", "resource"]),

  quotaTransactions: defineTable({
    userId: v.id("users"),
    resource: v.string(),
    delta: v.number(),
    reason: v.optional(v.string()),
  }).index("by_user", ["userId"]),

  quotaAlerts: defineTable({
    userId: v.id("users"),
    resource: v.string(),
    threshold: v.number(),
    triggered: v.boolean(),
    triggeredAt: v.optional(v.number()),
  }).index("by_user", ["userId"]),

  // -------------------------------------------------------------------------
  // Projects
  // -------------------------------------------------------------------------
  projects: defineTable({
    name: v.string(),
    description: v.optional(v.string()),
    ownerId: v.id("users"),
    organizationId: v.optional(v.id("organizations")),
    metadata: v.optional(v.record(v.string(), v.any())),
    isArchived: v.boolean(),
  })
    .index("by_owner", ["ownerId"])
    .index("by_org", ["organizationId"]),

  projectFiles: defineTable({
    projectId: v.id("projects"),
    path: v.string(),
    content: v.string(),
    language: v.optional(v.string()),
    updatedBy: v.optional(v.id("users")),
  })
    .index("by_project", ["projectId"])
    .index("by_project_path", ["projectId", "path"]),

  chatMessages: defineTable({
    projectId: v.id("projects"),
    userId: v.id("users"),
    role: v.string(), // user | assistant | system
    content: v.string(),
    metadata: v.optional(v.record(v.string(), v.any())),
  }).index("by_project", ["projectId"]),

  // -------------------------------------------------------------------------
  // Prompt History
  // -------------------------------------------------------------------------
  promptHistory: defineTable({
    promptId: v.string(),
    userId: v.string(),
    projectId: v.optional(v.string()),
    sessionId: v.optional(v.string()),
    prompt: v.string(),
    systemPrompt: v.optional(v.string()),
    context: v.optional(v.record(v.string(), v.any())),
    response: v.optional(v.string()),
    generatedCode: v.optional(v.string()),
    generatedFiles: v.optional(v.array(v.record(v.string(), v.any()))),
    promptType: v.string(),
    status: v.string(),
    model: v.optional(v.string()),
    promptTokens: v.optional(v.number()),
    completionTokens: v.optional(v.number()),
    totalTokens: v.optional(v.number()),
    durationMs: v.optional(v.number()),
    error: v.optional(v.string()),
    tags: v.array(v.string()),
    isFavorite: v.boolean(),
    createdAt: v.number(),
  })
    .index("by_user", ["userId"])
    .index("by_user_project", ["userId", "projectId"])
    .index("by_prompt_id", ["promptId"]),

  // -------------------------------------------------------------------------
  // Chat Sessions
  // -------------------------------------------------------------------------
  chatSessions: defineTable({
    userId: v.string(),
    sessionId: v.string(),
    messageCount: v.optional(v.number()),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_user", ["userId"])
    .index("by_session", ["sessionId"])
    .index("by_user_session", ["userId", "sessionId"]),

  chatSessionMessages: defineTable({
    sessionId: v.string(),
    role: v.string(),
    content: v.string(),
    timestamp: v.number(),
    metadata: v.optional(v.record(v.string(), v.any())),
  })
    .index("by_session", ["sessionId"])
    .index("by_session_ts", ["sessionId", "timestamp"]),

  // -------------------------------------------------------------------------
  // Checkpoints (Version Snapshots)
  // -------------------------------------------------------------------------
  checkpoints: defineTable({
    projectId: v.string(),
    description: v.string(),
    filesSnapshot: v.record(v.string(), v.any()),
    createdBy: v.optional(v.string()),
  }).index("by_project", ["projectId"]),

  // -------------------------------------------------------------------------
  // Collaborators
  // -------------------------------------------------------------------------
  collaborators: defineTable({
    projectId: v.string(),
    userEmail: v.string(),
    role: v.string(),
    accepted: v.boolean(),
    invitedAt: v.number(),
  })
    .index("by_project", ["projectId"])
    .index("by_email", ["userEmail"]),

  // -------------------------------------------------------------------------
  // Deployments
  // -------------------------------------------------------------------------
  deployments: defineTable({
    projectId: v.string(),
    provider: v.string(),
    url: v.string(),
    subdomain: v.optional(v.string()),
    status: v.string(),
    config: v.optional(v.record(v.string(), v.any())),
  }).index("by_project", ["projectId"]),

  // -------------------------------------------------------------------------
  // Project Memory
  // -------------------------------------------------------------------------
  projectMemory: defineTable({
    projectId: v.optional(v.string()),
    memoryType: v.string(), // adr | spec | session | decision | error | solution
    title: v.string(),
    content: v.string(),
    tags: v.array(v.string()),
    metadata: v.optional(v.record(v.string(), v.any())),
    userId: v.optional(v.id("users")),
  })
    .index("by_project", ["projectId"])
    .index("by_type", ["memoryType"])
    .index("by_project_type", ["projectId", "memoryType"]),

  // =========================================================================
  // APP PLATFORM (Phase L)
  // =========================================================================

  // -------------------------------------------------------------------------
  // Apps — Published applications on the platform
  // -------------------------------------------------------------------------
  apps: defineTable({
    developerId: v.string(),
    name: v.string(),
    slug: v.string(),
    description: v.string(),
    category: v.string(),
    type: v.string(), // plugin | fullstack | kernel
    latestVersion: v.string(),
    icon: v.optional(v.string()),
    screenshots: v.array(v.string()),
    pricing: v.string(), // free | paid | subscription
    price: v.number(),
    revenueSharePercent: v.number(),
    status: v.string(), // draft | review | published | suspended
    downloads: v.number(),
    avgRating: v.number(),
    ratingSum: v.optional(v.number()),
    ratingCount: v.optional(v.number()),
    permissions: v.array(v.string()),
    manifest: v.optional(v.record(v.string(), v.any())),
  })
    .index("by_slug", ["slug"])
    .index("by_developer", ["developerId"])
    .index("by_category", ["category"])
    .index("by_status", ["status"])
    .searchIndex("search_apps", { searchField: "name", filterFields: ["status"] }),

  // -------------------------------------------------------------------------
  // App Versions — Version history for each app
  // -------------------------------------------------------------------------
  appVersions: defineTable({
    appId: v.id("apps"),
    version: v.string(),
    changelog: v.string(),
    manifest: v.record(v.string(), v.any()),
    buildArtifact: v.optional(v.string()),
    status: v.string(), // draft | review | approved | rejected
    reviewNotes: v.optional(v.string()),
    publishedAt: v.optional(v.number()),
  })
    .index("by_app", ["appId"])
    .index("by_app_version", ["appId", "version"]),

  // -------------------------------------------------------------------------
  // App Installations — Per-org app installations
  // -------------------------------------------------------------------------
  appInstallations: defineTable({
    appId: v.id("apps"),
    organizationId: v.id("organizations"),
    installedBy: v.string(),
    version: v.string(),
    enabled: v.boolean(),
    grantedScopes: v.array(v.string()),
    config: v.optional(v.record(v.string(), v.any())),
    installedAt: v.number(),
  })
    .index("by_org", ["organizationId"])
    .index("by_app", ["appId"])
    .index("by_org_app", ["organizationId", "appId"]),

  // -------------------------------------------------------------------------
  // App Permissions — OAuth 2.0 scope grants
  // -------------------------------------------------------------------------
  appPermissions: defineTable({
    appId: v.id("apps"),
    organizationId: v.id("organizations"),
    userId: v.string(),
    scopes: v.array(v.string()),
    grantedAt: v.number(),
    expiresAt: v.optional(v.number()),
  })
    .index("by_app_org", ["appId", "organizationId"])
    .index("by_user", ["userId"]),

  // -------------------------------------------------------------------------
  // App Reviews — User ratings and reviews
  // -------------------------------------------------------------------------
  appReviews: defineTable({
    appId: v.id("apps"),
    userId: v.string(),
    rating: v.number(), // 1-5
    title: v.optional(v.string()),
    body: v.optional(v.string()),
    helpfulVotes: v.number(),
  })
    .index("by_app", ["appId"])
    .index("by_user", ["userId"]),

  // -------------------------------------------------------------------------
  // Developers — Developer accounts
  // -------------------------------------------------------------------------
  developers: defineTable({
    userId: v.string(),
    displayName: v.string(),
    email: v.string(),
    website: v.optional(v.string()),
    bio: v.optional(v.string()),
    stripeConnectId: v.optional(v.string()),
    verified: v.boolean(),
    totalEarnings: v.number(),
    totalApps: v.number(),
  })
    .index("by_user", ["userId"])
    .index("by_email", ["email"]),

  // -------------------------------------------------------------------------
  // Developer Payouts — Revenue share payments
  // -------------------------------------------------------------------------
  developerPayouts: defineTable({
    developerId: v.id("developers"),
    amount: v.number(),
    currency: v.string(),
    status: v.string(), // pending | processing | completed | failed
    stripeTransferId: v.optional(v.string()),
    periodStart: v.number(),
    periodEnd: v.number(),
  })
    .index("by_developer", ["developerId"])
    .index("by_status", ["status"]),

  // -------------------------------------------------------------------------
  // App Usage Metrics — Per-app usage tracking
  // -------------------------------------------------------------------------
  appUsageMetrics: defineTable({
    appId: v.id("apps"),
    organizationId: v.id("organizations"),
    metricName: v.string(), // api_calls | active_users | compute_time
    value: v.number(),
    periodStart: v.number(),
    periodEnd: v.number(),
  })
    .index("by_app", ["appId"])
    .index("by_org", ["organizationId"])
    .index("by_app_org", ["appId", "organizationId"]),

  // =========================================================================
  // VBUILDER — Real-Time Collaboration (Subsystem 3.4, Phase 3.0)
  // =========================================================================

  // ─── Yjs Document Registry — one entry per collaborative file ───
  yjsDocuments: defineTable({
    projectId: v.id("projects"),
    filePath: v.string(),           // e.g. "src/App.tsx"
    stateVector: v.optional(v.bytes()), // Yjs encoded state vector (for sync protocol)
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_project", ["projectId"])
    .index("by_project_file", ["projectId", "filePath"]),

  // ─── Yjs Updates — incremental CRDT deltas ───
  yjsUpdates: defineTable({
    documentId: v.id("yjsDocuments"),
    update: v.bytes(),               // Yjs encoded update (Uint8Array)
    clientId: v.string(),            // Clerk user ID of sender
    seq: v.number(),                 // Monotonic sequence number per document
    createdAt: v.number(),
  })
    .index("by_document", ["documentId"])
    .index("by_document_seq", ["documentId", "seq"]),

  // ─── Presence — real-time cursor positions & active users ───
  presence: defineTable({
    projectId: v.id("projects"),
    userId: v.string(),              // Clerk user ID
    displayName: v.string(),
    avatarUrl: v.optional(v.string()),
    color: v.string(),               // Hex cursor color (assigned deterministically)
    filePath: v.optional(v.string()), // Currently open file
    cursorLine: v.optional(v.number()),
    cursorColumn: v.optional(v.number()),
    selectionStartLine: v.optional(v.number()),
    selectionStartColumn: v.optional(v.number()),
    selectionEndLine: v.optional(v.number()),
    selectionEndColumn: v.optional(v.number()),
    lastHeartbeat: v.number(),       // Epoch ms — stale after 30s
    isOnline: v.boolean(),
  })
    .index("by_project", ["projectId"])
    .index("by_project_user", ["projectId", "userId"]),

  // =========================================================================
  // VBUILDER — Expert-in-the-Loop (IE-2, Phase 2.0)
  // =========================================================================
  expertRequests: defineTable({
    userId: v.id("users"),
    projectId: v.id("projects"),
    buildId: v.optional(v.string()),
    status: v.union(
      v.literal("pending"),
      v.literal("claimed"),
      v.literal("resolved"),
      v.literal("expired")
    ),
    context: v.optional(v.record(v.string(), v.any())),  // ExpertContext snapshot (≤900KB)
    claimedBy: v.optional(v.string()), // expert user ID
    claimedAt: v.optional(v.number()),
    resolution: v.optional(v.record(v.string(), v.any())),  // { patches, summary, resolved_by }
    resolvedAt: v.optional(v.number()),
    userDescription: v.optional(v.string()),
  })
    .index("by_user", ["userId"])
    .index("by_project", ["projectId"])
    .index("by_status", ["status"]),

  // =========================================================================
  // VBUILDER — Build Pipeline Tracking (Phase 1.0)
  // =========================================================================

  // ===== BUILDS — tracks each build invocation =====
  builds: defineTable({
    projectId: v.id("projects"),
    userId: v.id("users"),
    status: v.union(
      v.literal("queued"),
      v.literal("running"),
      v.literal("completed"),
      v.literal("failed"),
      v.literal("cancelled")
    ),
    requirements: v.string(),
    totalCost: v.optional(v.number()),
    totalTokens: v.optional(v.number()),
    totalDuration: v.optional(v.number()),
    costBreakdown: v.optional(v.record(v.string(), v.any())),
    filesGenerated: v.optional(v.number()),
    securityScore: v.optional(v.number()),
    completenessScore: v.optional(v.number()),
    errorMessage: v.optional(v.string()),
    vos3Metadata: v.optional(v.object({
      kernelPid: v.optional(v.number()),
      resourceQuotaUsed: v.optional(v.object({
        cpuTimeMs: v.number(),
        memoryPagesAllocated: v.number(),
        memoryPeakBytes: v.number(),
        ipcMessagesSent: v.number(),
        syscallsExecuted: v.number(),
      })),
      osSecurityContext: v.optional(v.object({
        sandboxId: v.optional(v.string()),
        allowedSyscalls: v.optional(v.array(v.string())),
        securityViolations: v.optional(v.number()),
        isolationLevel: v.optional(v.string()),
        uid: v.optional(v.number()),
        gid: v.optional(v.number()),
      })),
    })),
    createdAt: v.number(),
    completedAt: v.optional(v.number()),
  })
    .index("by_project", ["projectId"])
    .index("by_user", ["userId"])
    .index("by_status", ["status"])
    .index("by_project_status", ["projectId", "status"]),

  // ===== AGENT STATUS — real-time per-node progress during a build =====
  agentStatus: defineTable({
    buildId: v.id("builds"),
    agentName: v.string(),
    model: v.optional(v.string()),
    status: v.union(
      v.literal("idle"),
      v.literal("running"),
      v.literal("completed"),
      v.literal("failed"),
      v.literal("skipped")
    ),
    progressPercent: v.optional(v.number()),
    progressMessage: v.optional(v.string()),
    cost: v.optional(v.number()),
    tokens: v.optional(v.number()),
    duration: v.optional(v.number()),
    filesCreated: v.optional(v.array(v.string())),
    errorMessage: v.optional(v.string()),
    vos3Metadata: v.optional(v.object({
      kernelPid: v.optional(v.number()),
      resourceQuotaUsed: v.optional(v.object({
        cpuTimeMs: v.number(),
        memoryPagesAllocated: v.number(),
        memoryPeakBytes: v.number(),
      })),
      osSecurityContext: v.optional(v.object({
        sandboxId: v.optional(v.string()),
        allowedSyscalls: v.optional(v.array(v.string())),
        securityViolations: v.optional(v.number()),
        isolationLevel: v.optional(v.string()),
      })),
    })),
    startedAt: v.optional(v.number()),
    completedAt: v.optional(v.number()),
  })
    .index("by_build", ["buildId"])
    .index("by_build_agent", ["buildId", "agentName"]),

  // ===== CONTEXT SNAPSHOTS — agent state checkpoints for build resilience =====
  contextSnapshots: defineTable({
    buildId: v.id("builds"),
    checkpointNode: v.string(),
    serializedState: v.string(),
    decisionLog: v.array(
      v.object({
        agent: v.string(),
        decision: v.string(),
        reasoning: v.optional(v.string()),
        timestamp: v.number(),
      })
    ),
    taskProgress: v.object({
      totalTasks: v.number(),
      completedTasks: v.number(),
      currentTask: v.string(),
      completedNodes: v.array(v.string()),
      pendingNodes: v.array(v.string()),
    }),
    accumulatedCost: v.optional(v.number()),
    accumulatedTokens: v.optional(v.number()),
    createdAt: v.number(),
    sizeBytes: v.optional(v.number()),
  })
    .index("by_build", ["buildId"])
    .index("by_build_node", ["buildId", "checkpointNode"]),

  // ===== WEBHOOK_SEEN — Resilience-Matrix F10 webhook idempotency =====
  // Each Clerk (or Stripe / Convex / etc.) webhook delivery is recorded
  // by its provider-supplied unique ID before the handler runs. Re-
  // delivery attempts (provider retry on 5xx, dropped-connection
  // resend) hit the existing row and are short-circuited. Rows are
  // pruned by a scheduled mutation after `expiresAt`.
  webhook_seen: defineTable({
    // Composite key: provider name + provider's per-event ID.
    // Examples: provider="clerk", externalId="evt_2pX..." (svix-id).
    provider:    v.string(),
    externalId:  v.string(),
    // Event-type for diagnostics; not part of the dedup key.
    eventType:   v.optional(v.string()),
    seenAt:      v.number(),
    // Soft-expiry — rows older than this can be pruned.
    expiresAt:   v.number(),
    // Optional response we returned the first time, so retries can be
    // answered identically without re-running side-effects.
    cachedResponse: v.optional(v.string()),
  })
    .index("by_provider_external", ["provider", "externalId"])
    .index("by_expiry", ["expiresAt"]),
});
