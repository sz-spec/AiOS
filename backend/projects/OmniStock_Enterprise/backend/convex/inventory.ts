// OmniStock Enterprise - Inventory Functions
import { query, mutation } from "./_generated/server";
import { v } from "convex/values";

// ============================================================================
// QUERIES
// ============================================================================

export const getProducts = query({
  args: { orgId: v.id("organizations") },
  handler: async (ctx, args) => {
    return await ctx.db
      .query("products")
      .withIndex("by_org", (q) => q.eq("orgId", args.orgId))
      .filter((q) => q.eq(q.field("isActive"), true))
      .collect();
  },
});

export const getInventoryByWarehouse = query({
  args: { warehouseId: v.id("warehouses") },
  handler: async (ctx, args) => {
    const inventory = await ctx.db
      .query("inventory")
      .withIndex("by_warehouse", (q) => q.eq("warehouseId", args.warehouseId))
      .collect();

    // Enrich with product details
    const enriched = await Promise.all(
      inventory.map(async (inv) => {
        const product = await ctx.db.get(inv.productId);
        return { ...inv, product };
      })
    );

    return enriched;
  },
});

export const getLowStockItems = query({
  args: { orgId: v.id("organizations") },
  handler: async (ctx, args) => {
    const inventory = await ctx.db
      .query("inventory")
      .withIndex("by_org", (q) => q.eq("orgId", args.orgId))
      .collect();

    const lowStock = inventory.filter((inv) => inv.quantity <= inv.minStock && inv.quantity > 0);

    // Enrich with product and warehouse details
    const enriched = await Promise.all(
      lowStock.map(async (inv) => {
        const [product, warehouse] = await Promise.all([
          ctx.db.get(inv.productId),
          ctx.db.get(inv.warehouseId),
        ]);
        return { ...inv, product, warehouse };
      })
    );

    return enriched;
  },
});

export const getOutOfStockItems = query({
  args: { orgId: v.id("organizations") },
  handler: async (ctx, args) => {
    const inventory = await ctx.db
      .query("inventory")
      .withIndex("by_org", (q) => q.eq("orgId", args.orgId))
      .collect();

    const outOfStock = inventory.filter((inv) => inv.quantity === 0);

    const enriched = await Promise.all(
      outOfStock.map(async (inv) => {
        const [product, warehouse] = await Promise.all([
          ctx.db.get(inv.productId),
          ctx.db.get(inv.warehouseId),
        ]);
        return { ...inv, product, warehouse };
      })
    );

    return enriched;
  },
});

export const getAlerts = query({
  args: {
    orgId: v.id("organizations"),
    status: v.optional(v.union(v.literal("unresolved"), v.literal("acknowledged"), v.literal("resolved"))),
  },
  handler: async (ctx, args) => {
    let alertsQuery = ctx.db
      .query("alerts")
      .withIndex("by_org", (q) => q.eq("orgId", args.orgId));

    if (args.status) {
      alertsQuery = ctx.db
        .query("alerts")
        .withIndex("by_org_status", (q) => q.eq("orgId", args.orgId).eq("status", args.status));
    }

    const alerts = await alertsQuery.order("desc").collect();

    // Enrich with product details
    const enriched = await Promise.all(
      alerts.map(async (alert) => {
        const product = alert.productId ? await ctx.db.get(alert.productId) : null;
        const warehouse = alert.warehouseId ? await ctx.db.get(alert.warehouseId) : null;
        return { ...alert, product, warehouse };
      })
    );

    return enriched;
  },
});

export const getWarehouses = query({
  args: { orgId: v.id("organizations") },
  handler: async (ctx, args) => {
    return await ctx.db
      .query("warehouses")
      .withIndex("by_org", (q) => q.eq("orgId", args.orgId))
      .filter((q) => q.eq(q.field("isActive"), true))
      .collect();
  },
});

export const getWarehouseStats = query({
  args: { orgId: v.id("organizations") },
  handler: async (ctx, args) => {
    const warehouses = await ctx.db
      .query("warehouses")
      .withIndex("by_org", (q) => q.eq("orgId", args.orgId))
      .collect();

    const stats = await Promise.all(
      warehouses.map(async (wh) => {
        const inventory = await ctx.db
          .query("inventory")
          .withIndex("by_warehouse", (q) => q.eq("warehouseId", wh._id))
          .collect();

        const totalItems = inventory.reduce((sum, inv) => sum + inv.quantity, 0);
        const totalValue = await Promise.all(
          inventory.map(async (inv) => {
            const product = await ctx.db.get(inv.productId);
            return inv.quantity * (product?.price || 0);
          })
        ).then((values) => values.reduce((sum, v) => sum + v, 0));

        const utilization = Math.round((totalItems / wh.capacity) * 100);

        return {
          warehouse: wh,
          totalItems,
          totalValue,
          utilization: Math.min(utilization, 100),
          skuCount: inventory.length,
        };
      })
    );

    return stats;
  },
});

export const getRecentMovements = query({
  args: {
    orgId: v.id("organizations"),
    limit: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    const movements = await ctx.db
      .query("stockMovements")
      .withIndex("by_org", (q) => q.eq("orgId", args.orgId))
      .order("desc")
      .take(args.limit || 20);

    const enriched = await Promise.all(
      movements.map(async (mov) => {
        const [product, warehouse, user] = await Promise.all([
          ctx.db.get(mov.productId),
          ctx.db.get(mov.warehouseId),
          ctx.db.get(mov.userId),
        ]);
        return { ...mov, product, warehouse, user };
      })
    );

    return enriched;
  },
});

// ============================================================================
// MUTATIONS
// ============================================================================

export const adjustStock = mutation({
  args: {
    inventoryId: v.id("inventory"),
    quantity: v.number(),
    reason: v.string(),
    userId: v.id("users"),
  },
  handler: async (ctx, args) => {
    const inventory = await ctx.db.get(args.inventoryId);
    if (!inventory) throw new Error("Inventory record not found");

    const newQuantity = inventory.quantity + args.quantity;
    if (newQuantity < 0) throw new Error("Cannot reduce stock below zero");

    // Update inventory
    await ctx.db.patch(args.inventoryId, {
      quantity: newQuantity,
      updatedAt: Date.now(),
    });

    // Record movement
    await ctx.db.insert("stockMovements", {
      orgId: inventory.orgId,
      productId: inventory.productId,
      warehouseId: inventory.warehouseId,
      type: args.quantity > 0 ? "in" : args.quantity < 0 ? "out" : "adjustment",
      quantity: args.quantity,
      reason: args.reason,
      userId: args.userId,
      createdAt: Date.now(),
    });

    // Check for alerts
    if (newQuantity === 0) {
      await ctx.db.insert("alerts", {
        orgId: inventory.orgId,
        type: "out_of_stock",
        severity: "critical",
        message: `Product is now out of stock`,
        productId: inventory.productId,
        warehouseId: inventory.warehouseId,
        status: "unresolved",
        createdAt: Date.now(),
      });
    } else if (newQuantity <= inventory.minStock) {
      await ctx.db.insert("alerts", {
        orgId: inventory.orgId,
        type: "low_stock",
        severity: "high",
        message: `Stock level (${newQuantity}) is below minimum (${inventory.minStock})`,
        productId: inventory.productId,
        warehouseId: inventory.warehouseId,
        status: "unresolved",
        createdAt: Date.now(),
      });
    }

    return { success: true, newQuantity };
  },
});

export const resolveAlert = mutation({
  args: {
    alertId: v.id("alerts"),
    userId: v.id("users"),
  },
  handler: async (ctx, args) => {
    await ctx.db.patch(args.alertId, {
      status: "resolved",
      resolvedBy: args.userId,
      resolvedAt: Date.now(),
    });

    return { success: true };
  },
});

export const createProduct = mutation({
  args: {
    orgId: v.id("organizations"),
    sku: v.string(),
    name: v.string(),
    category: v.string(),
    price: v.number(),
    cost: v.number(),
    description: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    // Check for duplicate SKU
    const existing = await ctx.db
      .query("products")
      .withIndex("by_org_sku", (q) => q.eq("orgId", args.orgId).eq("sku", args.sku))
      .first();

    if (existing) throw new Error(`Product with SKU ${args.sku} already exists`);

    const now = Date.now();
    const productId = await ctx.db.insert("products", {
      orgId: args.orgId,
      sku: args.sku,
      name: args.name,
      category: args.category,
      price: args.price,
      cost: args.cost,
      description: args.description,
      isActive: true,
      createdAt: now,
      updatedAt: now,
    });

    return { success: true, productId };
  },
});

export const transferStock = mutation({
  args: {
    orgId: v.id("organizations"),
    productId: v.id("products"),
    fromWarehouseId: v.id("warehouses"),
    toWarehouseId: v.id("warehouses"),
    quantity: v.number(),
    userId: v.id("users"),
  },
  handler: async (ctx, args) => {
    if (args.quantity <= 0) throw new Error("Quantity must be positive");
    if (args.fromWarehouseId === args.toWarehouseId) throw new Error("Cannot transfer to same warehouse");

    // Get source inventory
    const sourceInv = await ctx.db
      .query("inventory")
      .withIndex("by_org_product_warehouse", (q) =>
        q.eq("orgId", args.orgId).eq("productId", args.productId).eq("warehouseId", args.fromWarehouseId)
      )
      .first();

    if (!sourceInv) throw new Error("Source inventory not found");
    if (sourceInv.quantity < args.quantity) throw new Error("Insufficient stock for transfer");

    // Get or create destination inventory
    let destInv = await ctx.db
      .query("inventory")
      .withIndex("by_org_product_warehouse", (q) =>
        q.eq("orgId", args.orgId).eq("productId", args.productId).eq("warehouseId", args.toWarehouseId)
      )
      .first();

    const now = Date.now();

    if (!destInv) {
      // Create new inventory record at destination
      await ctx.db.insert("inventory", {
        orgId: args.orgId,
        productId: args.productId,
        warehouseId: args.toWarehouseId,
        quantity: args.quantity,
        minStock: sourceInv.minStock,
        maxStock: sourceInv.maxStock,
        reorderPoint: sourceInv.reorderPoint,
        updatedAt: now,
      });
    } else {
      // Update existing destination
      await ctx.db.patch(destInv._id, {
        quantity: destInv.quantity + args.quantity,
        updatedAt: now,
      });
    }

    // Reduce source
    await ctx.db.patch(sourceInv._id, {
      quantity: sourceInv.quantity - args.quantity,
      updatedAt: now,
    });

    // Record movements
    await ctx.db.insert("stockMovements", {
      orgId: args.orgId,
      productId: args.productId,
      warehouseId: args.fromWarehouseId,
      type: "transfer",
      quantity: -args.quantity,
      reason: `Transfer to ${args.toWarehouseId}`,
      userId: args.userId,
      createdAt: now,
    });

    await ctx.db.insert("stockMovements", {
      orgId: args.orgId,
      productId: args.productId,
      warehouseId: args.toWarehouseId,
      type: "transfer",
      quantity: args.quantity,
      reason: `Transfer from ${args.fromWarehouseId}`,
      userId: args.userId,
      createdAt: now,
    });

    return { success: true };
  },
});
