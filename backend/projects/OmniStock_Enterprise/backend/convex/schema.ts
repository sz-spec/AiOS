// OmniStock Enterprise - Convex Schema
import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  // ============================================================================
  // ORGANIZATIONS
  // ============================================================================
  organizations: defineTable({
    name: v.string(),
    slug: v.string(),
    plan: v.union(v.literal("free"), v.literal("pro"), v.literal("enterprise")),
    settings: v.optional(v.object({
      lowStockThreshold: v.optional(v.number()),
      currency: v.optional(v.string()),
      timezone: v.optional(v.string()),
    })),
    createdAt: v.number(),
  }).index("by_slug", ["slug"]),

  // ============================================================================
  // USERS & ROLES
  // ============================================================================
  users: defineTable({
    orgId: v.id("organizations"),
    clerkId: v.string(),
    email: v.string(),
    name: v.string(),
    role: v.union(v.literal("admin"), v.literal("manager"), v.literal("viewer")),
    avatarUrl: v.optional(v.string()),
    createdAt: v.number(),
  })
    .index("by_org", ["orgId"])
    .index("by_clerk", ["clerkId"]),

  // ============================================================================
  // PRODUCTS / INVENTORY
  // ============================================================================
  products: defineTable({
    orgId: v.id("organizations"),
    name: v.string(),
    sku: v.string(),
    price: v.number(),
    category: v.string(),
    description: v.optional(v.string()),
    cost: v.optional(v.number()),
    imageUrl: v.optional(v.string()),
    tags: v.optional(v.array(v.string())),
    isActive: v.optional(v.boolean()),
    createdAt: v.optional(v.number()),
    updatedAt: v.optional(v.number()),
  })
    .index("by_org", ["orgId"])
    .index("by_org_sku", ["orgId", "sku"])
    .index("by_org_category", ["orgId", "category"]),

  // ============================================================================
  // WAREHOUSES
  // ============================================================================
  warehouses: defineTable({
    orgId: v.id("organizations"),
    name: v.string(),
    location: v.string(), // Simple location: "North", "East Wing", etc.
    code: v.optional(v.string()),
    address: v.optional(v.string()),
    city: v.optional(v.string()),
    country: v.optional(v.string()),
    capacity: v.optional(v.number()),
    isActive: v.optional(v.boolean()),
    createdAt: v.optional(v.number()),
  })
    .index("by_org", ["orgId"])
    .index("by_org_location", ["orgId", "location"]),

  // ============================================================================
  // INVENTORY (Stock Levels per Warehouse)
  // ============================================================================
  inventory: defineTable({
    orgId: v.optional(v.id("organizations")),
    productId: v.id("products"),
    warehouseId: v.id("warehouses"),
    quantity: v.number(),
    lowStockThreshold: v.number(), // Alert when quantity falls below this
    minStock: v.optional(v.number()),
    maxStock: v.optional(v.number()),
    reorderPoint: v.optional(v.number()),
    location: v.optional(v.string()), // e.g., "Aisle 5, Shelf 12"
    lastCountedAt: v.optional(v.number()),
    updatedAt: v.optional(v.number()),
  })
    .index("by_product", ["productId"])
    .index("by_warehouse", ["warehouseId"]),

  // ============================================================================
  // STOCK MOVEMENTS
  // ============================================================================
  stockMovements: defineTable({
    orgId: v.id("organizations"),
    productId: v.id("products"),
    warehouseId: v.id("warehouses"),
    type: v.union(
      v.literal("in"),
      v.literal("out"),
      v.literal("transfer"),
      v.literal("adjustment"),
      v.literal("return")
    ),
    quantity: v.number(), // positive for in, negative for out
    reason: v.string(),
    reference: v.optional(v.string()), // Order ID, PO number, etc.
    userId: v.id("users"),
    createdAt: v.number(),
  })
    .index("by_org", ["orgId"])
    .index("by_product", ["productId"])
    .index("by_warehouse", ["warehouseId"])
    .index("by_org_date", ["orgId", "createdAt"]),

  // ============================================================================
  // SUPPLIERS / CONTACTS
  // ============================================================================
  contacts: defineTable({
    orgId: v.id("organizations"),
    type: v.union(v.literal("supplier"), v.literal("customer"), v.literal("partner")),
    name: v.string(),
    company: v.optional(v.string()),
    email: v.string(),
    phone: v.optional(v.string()),
    address: v.optional(v.string()),
    leadTimeDays: v.optional(v.number()),
    notes: v.optional(v.string()),
    isActive: v.boolean(),
    createdAt: v.number(),
  })
    .index("by_org", ["orgId"])
    .index("by_org_type", ["orgId", "type"]),

  // ============================================================================
  // PURCHASE ORDERS
  // ============================================================================
  purchaseOrders: defineTable({
    orgId: v.id("organizations"),
    supplierId: v.id("contacts"),
    warehouseId: v.id("warehouses"),
    poNumber: v.string(),
    status: v.union(
      v.literal("draft"),
      v.literal("pending"),
      v.literal("approved"),
      v.literal("ordered"),
      v.literal("partial"),
      v.literal("received"),
      v.literal("cancelled")
    ),
    items: v.array(v.object({
      productId: v.id("products"),
      quantity: v.number(),
      unitCost: v.number(),
      receivedQty: v.optional(v.number()),
    })),
    totalAmount: v.number(),
    expectedDate: v.optional(v.number()),
    receivedDate: v.optional(v.number()),
    notes: v.optional(v.string()),
    createdBy: v.id("users"),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_org", ["orgId"])
    .index("by_org_status", ["orgId", "status"])
    .index("by_supplier", ["supplierId"]),

  // ============================================================================
  // ALERTS
  // ============================================================================
  alerts: defineTable({
    orgId: v.id("organizations"),
    type: v.union(
      v.literal("low_stock"),
      v.literal("out_of_stock"),
      v.literal("overstock"),
      v.literal("expiring"),
      v.literal("reorder"),
      v.literal("system")
    ),
    severity: v.union(v.literal("low"), v.literal("medium"), v.literal("high"), v.literal("critical")),
    message: v.string(),
    productId: v.optional(v.id("products")),
    warehouseId: v.optional(v.id("warehouses")),
    status: v.union(v.literal("unresolved"), v.literal("acknowledged"), v.literal("resolved")),
    resolvedBy: v.optional(v.id("users")),
    resolvedAt: v.optional(v.number()),
    createdAt: v.number(),
  })
    .index("by_org", ["orgId"])
    .index("by_org_status", ["orgId", "status"])
    .index("by_org_severity", ["orgId", "severity"]),

  // ============================================================================
  // RECORDS (Generic Entity Records for V-Core)
  // ============================================================================
  entities: defineTable({
    orgId: v.id("organizations"),
    name: v.string(),
    slug: v.string(),
    fields: v.array(v.object({
      name: v.string(),
      type: v.string(),
      required: v.boolean(),
    })),
    createdAt: v.number(),
  })
    .index("by_org", ["orgId"])
    .index("by_org_slug", ["orgId", "slug"]),

  records: defineTable({
    orgId: v.id("organizations"),
    entityId: v.id("entities"),
    data: v.any(), // Flexible JSON data
    status: v.optional(v.string()),
    createdBy: v.optional(v.id("users")),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_org", ["orgId"])
    .index("by_entity", ["entityId"])
    .index("by_org_entity", ["orgId", "entityId"]),

  // ============================================================================
  // AUDIT LOG
  // ============================================================================
  auditLog: defineTable({
    orgId: v.id("organizations"),
    userId: v.id("users"),
    action: v.string(), // e.g., "product.create", "inventory.update"
    resourceType: v.string(),
    resourceId: v.string(),
    details: v.optional(v.any()),
    ipAddress: v.optional(v.string()),
    createdAt: v.number(),
  })
    .index("by_org", ["orgId"])
    .index("by_org_date", ["orgId", "createdAt"])
    .index("by_user", ["userId"]),
});
