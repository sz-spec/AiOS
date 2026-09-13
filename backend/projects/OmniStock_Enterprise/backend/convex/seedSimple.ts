// OmniStock Enterprise - Simple Seed (Matches User Pattern)
import { mutation } from "./_generated/server";

export const seedInventory = mutation({
  args: {},
  handler: async (ctx) => {
    // =========================================================================
    // 1. CREATE ORGANIZATION
    // =========================================================================
    const orgId = await ctx.db.insert("organizations", {
      name: "OmniStock Corp",
      slug: "omnistock",
      plan: "enterprise",
      createdAt: Date.now(),
    });

    // =========================================================================
    // 2. CREATE PRODUCTS
    // =========================================================================
    const products = [
      { name: "Industrial Drill Z-100", sku: "DRL-100", price: 299.99, category: "Tools" },
      { name: "Hydraulic Press HP-50", sku: "PRS-050", price: 1299.99, category: "Machinery" },
      { name: "Safety Helmet Pro", sku: "SAF-001", price: 49.99, category: "Safety" },
      { name: "Welding Machine Arc-200", sku: "WLD-200", price: 599.99, category: "Tools" },
      { name: "Industrial Gloves Pack", sku: "SAF-002", price: 29.99, category: "Safety" },
      { name: "Air Compressor 50L", sku: "CMP-050", price: 449.99, category: "Machinery" },
      { name: "Power Generator 5KW", sku: "GEN-005", price: 899.99, category: "Power" },
      { name: "LED Floodlight 200W", sku: "LGT-200", price: 79.99, category: "Lighting" },
    ];

    const productIds: Record<string, any> = {};
    for (const product of products) {
      const id = await ctx.db.insert("products", {
        orgId,
        name: product.name,
        sku: product.sku,
        price: product.price,
        category: product.category,
      });
      productIds[product.sku] = id;
    }

    console.log(`[Seed] Created ${products.length} products`);

    // =========================================================================
    // 3. CREATE WAREHOUSES (Simple Pattern)
    // =========================================================================
    const mainWarehouse = await ctx.db.insert("warehouses", {
      orgId,
      name: "Main Distribution Center",
      location: "North",
    });

    const eastWarehouse = await ctx.db.insert("warehouses", {
      orgId,
      name: "East Regional Hub",
      location: "East",
    });

    const westWarehouse = await ctx.db.insert("warehouses", {
      orgId,
      name: "West Coast Facility",
      location: "West",
    });

    console.log(`[Seed] Created 3 warehouses`);

    // =========================================================================
    // 4. CREATE INVENTORY (Connect Products <-> Warehouses)
    // =========================================================================
    const inventoryData = [
      // Main Warehouse
      { sku: "DRL-100", warehouse: mainWarehouse, quantity: 150, threshold: 20 },
      { sku: "PRS-050", warehouse: mainWarehouse, quantity: 25, threshold: 5 },
      { sku: "SAF-001", warehouse: mainWarehouse, quantity: 500, threshold: 100 },
      { sku: "WLD-200", warehouse: mainWarehouse, quantity: 45, threshold: 10 },
      // East Warehouse
      { sku: "SAF-002", warehouse: eastWarehouse, quantity: 300, threshold: 50 },
      { sku: "CMP-050", warehouse: eastWarehouse, quantity: 18, threshold: 5 },
      { sku: "DRL-100", warehouse: eastWarehouse, quantity: 75, threshold: 15 },
      // West Warehouse
      { sku: "GEN-005", warehouse: westWarehouse, quantity: 12, threshold: 3 },
      { sku: "LGT-200", warehouse: westWarehouse, quantity: 200, threshold: 30 },
      { sku: "PRS-050", warehouse: westWarehouse, quantity: 8, threshold: 2 },
    ];

    for (const inv of inventoryData) {
      await ctx.db.insert("inventory", {
        productId: productIds[inv.sku],
        warehouseId: inv.warehouse,
        quantity: inv.quantity,
        lowStockThreshold: inv.threshold,
      });
    }

    console.log(`[Seed] Created ${inventoryData.length} inventory records`);

    // =========================================================================
    // 5. CREATE ALERTS
    // =========================================================================
    await ctx.db.insert("alerts", {
      orgId,
      type: "low_stock",
      severity: "high",
      message: "Inventory low on Product A",
      status: "unresolved",
      createdAt: Date.now(),
    });

    await ctx.db.insert("alerts", {
      orgId,
      type: "out_of_stock",
      severity: "critical",
      message: "Hydraulic Press HP-50 out of stock in West facility",
      productId: productIds["PRS-050"],
      warehouseId: westWarehouse,
      status: "unresolved",
      createdAt: Date.now(),
    });

    console.log(`[Seed] Created 2 alerts`);

    // =========================================================================
    // 6. CREATE SUPPLIER (Contact)
    // =========================================================================
    await ctx.db.insert("contacts", {
      orgId,
      type: "supplier",
      name: "Global Supplies Inc",
      email: "sales@globalsupplies.com",
      isActive: true,
      createdAt: Date.now(),
    });

    await ctx.db.insert("contacts", {
      orgId,
      type: "supplier",
      name: "Industrial Parts Direct",
      email: "orders@industrialparts.com",
      phone: "+1-555-0123",
      isActive: true,
      createdAt: Date.now(),
    });

    console.log(`[Seed] Created 2 suppliers`);

    // =========================================================================
    // 7. CREATE ENTITY & RECORDS (V-Core Pattern)
    // =========================================================================
    const entityId = await ctx.db.insert("entities", {
      orgId,
      name: "Purchase Order",
      slug: "purchase-order",
      fields: [
        { name: "poNumber", type: "string", required: true },
        { name: "status", type: "select", required: true },
        { name: "amount", type: "number", required: true },
      ],
      createdAt: Date.now(),
    });

    await ctx.db.insert("records", {
      orgId,
      entityId,
      data: { poNumber: "PO-2026-001", status: "pending", amount: 500 },
      createdAt: Date.now(),
      updatedAt: Date.now(),
    });

    await ctx.db.insert("records", {
      orgId,
      entityId,
      data: { poNumber: "PO-2026-002", status: "approved", amount: 1250 },
      createdAt: Date.now(),
      updatedAt: Date.now(),
    });

    console.log(`[Seed] Created entity with 2 records`);

    // =========================================================================
    // DONE
    // =========================================================================
    console.log(`\n[Seed] ✅ Inventory seeded successfully!`);

    return {
      success: true,
      orgId,
      stats: {
        products: products.length,
        warehouses: 3,
        inventory: inventoryData.length,
        alerts: 2,
        suppliers: 2,
        records: 2,
      },
    };
  },
});
