// OmniStock Enterprise - Seed Data
import { mutation } from "./_generated/server";
import { v } from "convex/values";

export const seedDemoData = mutation({
  args: {},
  handler: async (ctx) => {
    const now = Date.now();

    // =========================================================================
    // 1. CREATE ORGANIZATION
    // =========================================================================
    const orgId = await ctx.db.insert("organizations", {
      name: "OmniStock Demo Corp",
      slug: "omnistock-demo",
      plan: "enterprise",
      settings: {
        lowStockThreshold: 20,
        currency: "USD",
        timezone: "America/Los_Angeles",
      },
      createdAt: now,
    });

    console.log(`[Seed] Created organization: ${orgId}`);

    // =========================================================================
    // 2. CREATE USERS
    // =========================================================================
    const adminUser = await ctx.db.insert("users", {
      orgId,
      clerkId: "dev_seed_user_admin",
      email: "admin@omnistock.demo",
      name: "John Admin",
      role: "admin",
      createdAt: now,
    });

    const managerUser = await ctx.db.insert("users", {
      orgId,
      clerkId: "dev_seed_user_manager",
      email: "manager@omnistock.demo",
      name: "Sarah Manager",
      role: "manager",
      createdAt: now,
    });

    console.log(`[Seed] Created ${2} users`);

    // =========================================================================
    // 3. CREATE WAREHOUSES
    // =========================================================================
    const warehouseSanJose = await ctx.db.insert("warehouses", {
      orgId,
      name: "San Jose Distribution Center",
      code: "SJ-DC1",
      address: "1234 Tech Blvd",
      city: "San Jose",
      country: "USA",
      capacity: 50000,
      isActive: true,
      createdAt: now,
    });

    const warehouseAustin = await ctx.db.insert("warehouses", {
      orgId,
      name: "Austin Fulfillment Hub",
      code: "ATX-FH1",
      address: "5678 Commerce Dr",
      city: "Austin",
      country: "USA",
      capacity: 35000,
      isActive: true,
      createdAt: now,
    });

    const warehouseSeattle = await ctx.db.insert("warehouses", {
      orgId,
      name: "Seattle Warehouse",
      code: "SEA-WH1",
      address: "9012 Industrial Way",
      city: "Seattle",
      country: "USA",
      capacity: 25000,
      isActive: true,
      createdAt: now,
    });

    console.log(`[Seed] Created ${3} warehouses`);

    // =========================================================================
    // 4. CREATE SUPPLIERS (CONTACTS)
    // =========================================================================
    const supplierGlobal = await ctx.db.insert("contacts", {
      orgId,
      type: "supplier",
      name: "Mike Johnson",
      company: "Global Supplies Inc",
      email: "sales@globalsupplies.com",
      phone: "+1-555-0100",
      leadTimeDays: 7,
      isActive: true,
      createdAt: now,
    });

    const supplierTech = await ctx.db.insert("contacts", {
      orgId,
      type: "supplier",
      name: "Lisa Chen",
      company: "TechParts Direct",
      email: "orders@techpartsdirect.com",
      phone: "+1-555-0200",
      leadTimeDays: 14,
      isActive: true,
      createdAt: now,
    });

    const supplierNetwork = await ctx.db.insert("contacts", {
      orgId,
      type: "supplier",
      name: "David Park",
      company: "NetworkGear Pro",
      email: "wholesale@networkgearpro.com",
      phone: "+1-555-0300",
      leadTimeDays: 5,
      isActive: true,
      createdAt: now,
    });

    console.log(`[Seed] Created ${3} suppliers`);

    // =========================================================================
    // 5. CREATE PRODUCTS
    // =========================================================================
    const products = [
      { sku: "NET-001", name: "Industrial Router V3", category: "Networking", price: 299.99, cost: 180.00 },
      { sku: "NET-045", name: "Ethernet Switch 48-Port", category: "Networking", price: 549.99, cost: 320.00 },
      { sku: "NET-201", name: "SFP+ Transceiver 10G", category: "Networking", price: 89.99, cost: 45.00 },
      { sku: "CBL-202", name: "Fiber Optic Cable 100m", category: "Cables", price: 149.99, cost: 75.00 },
      { sku: "CBL-089", name: "Cat6 Patch Panel 24-Port", category: "Cables", price: 79.99, cost: 40.00 },
      { sku: "SRV-088", name: "Server Rack 42U", category: "Infrastructure", price: 899.99, cost: 520.00 },
      { sku: "PWR-112", name: "UPS Battery Backup 3000VA", category: "Power", price: 699.99, cost: 420.00 },
      { sku: "PWR-056", name: "Rack Mount PDU 16-Outlet", category: "Power", price: 199.99, cost: 95.00 },
    ];

    const productIds: Record<string, any> = {};

    for (const product of products) {
      const id = await ctx.db.insert("products", {
        orgId,
        sku: product.sku,
        name: product.name,
        category: product.category,
        price: product.price,
        cost: product.cost,
        isActive: true,
        createdAt: now,
        updatedAt: now,
      });
      productIds[product.sku] = id;
    }

    console.log(`[Seed] Created ${products.length} products`);

    // =========================================================================
    // 6. CREATE INVENTORY RECORDS
    // =========================================================================
    const inventoryData = [
      // San Jose
      { sku: "NET-001", warehouse: warehouseSanJose, qty: 142, min: 50, max: 200, location: "Aisle 3, Shelf 12" },
      { sku: "SRV-088", warehouse: warehouseSanJose, qty: 0, min: 10, max: 30, location: "Zone B, Bay 5" },
      { sku: "PWR-056", warehouse: warehouseSanJose, qty: 8, min: 25, max: 100, location: "Aisle 7, Shelf 3" },
      // Austin
      { sku: "CBL-202", warehouse: warehouseAustin, qty: 12, min: 50, max: 200, location: "Aisle 1, Shelf 8" },
      { sku: "PWR-112", warehouse: warehouseAustin, qty: 234, min: 20, max: 100, location: "Zone A, Bay 2" },
      { sku: "NET-201", warehouse: warehouseAustin, qty: 0, min: 100, max: 500, location: "Aisle 4, Shelf 15" },
      // Seattle
      { sku: "NET-045", warehouse: warehouseSeattle, qty: 89, min: 30, max: 150, location: "Aisle 2, Shelf 6" },
      { sku: "CBL-089", warehouse: warehouseSeattle, qty: 45, min: 40, max: 120, location: "Aisle 1, Shelf 4" },
    ];

    for (const inv of inventoryData) {
      await ctx.db.insert("inventory", {
        orgId,
        productId: productIds[inv.sku],
        warehouseId: inv.warehouse,
        quantity: inv.qty,
        minStock: inv.min,
        maxStock: inv.max,
        reorderPoint: Math.floor(inv.min * 1.5),
        location: inv.location,
        updatedAt: now,
      });
    }

    console.log(`[Seed] Created ${inventoryData.length} inventory records`);

    // =========================================================================
    // 7. CREATE ALERTS
    // =========================================================================
    await ctx.db.insert("alerts", {
      orgId,
      type: "out_of_stock",
      severity: "critical",
      message: "Server Rack 42U is out of stock in San Jose DC",
      productId: productIds["SRV-088"],
      warehouseId: warehouseSanJose,
      status: "unresolved",
      createdAt: now,
    });

    await ctx.db.insert("alerts", {
      orgId,
      type: "low_stock",
      severity: "high",
      message: "Fiber Optic Cable 100m running low in Austin - only 12 units left",
      productId: productIds["CBL-202"],
      warehouseId: warehouseAustin,
      status: "unresolved",
      createdAt: now,
    });

    await ctx.db.insert("alerts", {
      orgId,
      type: "low_stock",
      severity: "medium",
      message: "Rack Mount PDU below minimum threshold in San Jose",
      productId: productIds["PWR-056"],
      warehouseId: warehouseSanJose,
      status: "acknowledged",
      createdAt: now - 86400000, // 1 day ago
    });

    await ctx.db.insert("alerts", {
      orgId,
      type: "overstock",
      severity: "low",
      message: "UPS Battery Backup exceeds max capacity in Austin (234/100)",
      productId: productIds["PWR-112"],
      warehouseId: warehouseAustin,
      status: "unresolved",
      createdAt: now,
    });

    await ctx.db.insert("alerts", {
      orgId,
      type: "out_of_stock",
      severity: "high",
      message: "SFP+ Transceiver out of stock in Austin",
      productId: productIds["NET-201"],
      warehouseId: warehouseAustin,
      status: "unresolved",
      createdAt: now,
    });

    console.log(`[Seed] Created ${5} alerts`);

    // =========================================================================
    // 8. CREATE ENTITY & RECORDS (V-Core Pattern)
    // =========================================================================
    const orderEntity = await ctx.db.insert("entities", {
      orgId,
      name: "Sales Order",
      slug: "sales-order",
      fields: [
        { name: "orderNumber", type: "string", required: true },
        { name: "customerName", type: "string", required: true },
        { name: "status", type: "select", required: true },
        { name: "amount", type: "number", required: true },
      ],
      createdAt: now,
    });

    // Sample order records
    const orders = [
      { orderNumber: "ORD-2026-001", customerName: "Acme Corp", status: "pending", amount: 12500 },
      { orderNumber: "ORD-2026-002", customerName: "TechStart Inc", status: "shipped", amount: 8900 },
      { orderNumber: "ORD-2026-003", customerName: "DataFlow Systems", status: "delivered", amount: 24300 },
      { orderNumber: "ORD-2026-004", customerName: "CloudNet Solutions", status: "pending", amount: 5670 },
    ];

    for (const order of orders) {
      await ctx.db.insert("records", {
        orgId,
        entityId: orderEntity,
        data: order,
        status: order.status,
        createdBy: adminUser,
        createdAt: now - Math.random() * 604800000, // Random time in last week
        updatedAt: now,
      });
    }

    console.log(`[Seed] Created entity with ${orders.length} records`);

    // =========================================================================
    // 9. CREATE PURCHASE ORDER
    // =========================================================================
    await ctx.db.insert("purchaseOrders", {
      orgId,
      supplierId: supplierNetwork,
      warehouseId: warehouseSanJose,
      poNumber: "PO-2026-0042",
      status: "ordered",
      items: [
        { productId: productIds["SRV-088"], quantity: 20, unitCost: 520.00 },
        { productId: productIds["PWR-056"], quantity: 50, unitCost: 95.00 },
      ],
      totalAmount: 15150.00,
      expectedDate: now + 604800000, // 1 week from now
      createdBy: managerUser,
      createdAt: now - 172800000, // 2 days ago
      updatedAt: now,
    });

    console.log(`[Seed] Created 1 purchase order`);

    // =========================================================================
    // 10. CREATE STOCK MOVEMENTS
    // =========================================================================
    const movements = [
      { product: "NET-001", warehouse: warehouseSanJose, type: "in", qty: 50, reason: "PO-2026-0038 received" },
      { product: "CBL-202", warehouse: warehouseAustin, type: "out", qty: -25, reason: "Order ORD-2026-001 shipped" },
      { product: "NET-045", warehouse: warehouseSeattle, type: "adjustment", qty: -3, reason: "Inventory audit correction" },
      { product: "PWR-112", warehouse: warehouseAustin, type: "in", qty: 100, reason: "Bulk restock from supplier" },
    ];

    for (const mov of movements) {
      await ctx.db.insert("stockMovements", {
        orgId,
        productId: productIds[mov.product],
        warehouseId: mov.warehouse,
        type: mov.type as any,
        quantity: mov.qty,
        reason: mov.reason,
        userId: adminUser,
        createdAt: now - Math.random() * 259200000, // Random time in last 3 days
      });
    }

    console.log(`[Seed] Created ${movements.length} stock movements`);

    // =========================================================================
    // DONE
    // =========================================================================
    console.log(`\n[Seed] ✅ Demo data seeded successfully!`);
    console.log(`[Seed] Organization ID: ${orgId}`);

    return {
      success: true,
      orgId,
      stats: {
        users: 2,
        warehouses: 3,
        suppliers: 3,
        products: products.length,
        inventory: inventoryData.length,
        alerts: 5,
        orders: orders.length,
        purchaseOrders: 1,
        movements: movements.length,
      },
    };
  },
});
