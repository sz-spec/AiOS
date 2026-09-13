// OmniStock Enterprise - Mock Data

import { Product, DashboardStats, Category, StockMovement } from '@/types/inventory';

export const mockProducts: Product[] = [
  {
    id: '1',
    sku: 'ELEC-001',
    name: 'Wireless Bluetooth Headphones',
    category: 'Electronics',
    quantity: 145,
    minStock: 50,
    maxStock: 300,
    price: 79.99,
    cost: 42.00,
    supplier: 'TechSupply Co.',
    location: 'Warehouse A - Shelf 12',
    lastRestocked: new Date('2026-02-10'),
    status: 'in_stock'
  },
  {
    id: '2',
    sku: 'ELEC-002',
    name: 'USB-C Fast Charger 65W',
    category: 'Electronics',
    quantity: 23,
    minStock: 100,
    maxStock: 500,
    price: 34.99,
    cost: 15.00,
    supplier: 'TechSupply Co.',
    location: 'Warehouse A - Shelf 14',
    lastRestocked: new Date('2026-02-01'),
    status: 'low_stock'
  },
  {
    id: '3',
    sku: 'FURN-001',
    name: 'Ergonomic Office Chair',
    category: 'Furniture',
    quantity: 0,
    minStock: 10,
    maxStock: 50,
    price: 299.99,
    cost: 180.00,
    supplier: 'OfficePro Ltd.',
    location: 'Warehouse B - Zone 3',
    lastRestocked: new Date('2026-01-15'),
    status: 'out_of_stock'
  },
  {
    id: '4',
    sku: 'FURN-002',
    name: 'Standing Desk 60"',
    category: 'Furniture',
    quantity: 67,
    minStock: 20,
    maxStock: 80,
    price: 449.99,
    cost: 280.00,
    supplier: 'OfficePro Ltd.',
    location: 'Warehouse B - Zone 2',
    lastRestocked: new Date('2026-02-12'),
    status: 'in_stock'
  },
  {
    id: '5',
    sku: 'ACCS-001',
    name: 'Laptop Sleeve 15"',
    category: 'Accessories',
    quantity: 312,
    minStock: 50,
    maxStock: 200,
    price: 24.99,
    cost: 8.00,
    supplier: 'AccessoryWorld',
    location: 'Warehouse A - Shelf 8',
    lastRestocked: new Date('2026-02-14'),
    status: 'overstock'
  },
  {
    id: '6',
    sku: 'ELEC-003',
    name: '4K Webcam Pro',
    category: 'Electronics',
    quantity: 89,
    minStock: 30,
    maxStock: 150,
    price: 149.99,
    cost: 75.00,
    supplier: 'TechSupply Co.',
    location: 'Warehouse A - Shelf 15',
    lastRestocked: new Date('2026-02-08'),
    status: 'in_stock'
  },
  {
    id: '7',
    sku: 'ACCS-002',
    name: 'Mechanical Keyboard RGB',
    category: 'Accessories',
    quantity: 156,
    minStock: 40,
    maxStock: 200,
    price: 89.99,
    cost: 45.00,
    supplier: 'AccessoryWorld',
    location: 'Warehouse A - Shelf 10',
    lastRestocked: new Date('2026-02-11'),
    status: 'in_stock'
  },
  {
    id: '8',
    sku: 'ELEC-004',
    name: 'Portable SSD 1TB',
    category: 'Electronics',
    quantity: 45,
    minStock: 60,
    maxStock: 200,
    price: 119.99,
    cost: 65.00,
    supplier: 'TechSupply Co.',
    location: 'Warehouse A - Shelf 16',
    lastRestocked: new Date('2026-02-05'),
    status: 'low_stock'
  }
];

export const mockStats: DashboardStats = {
  totalProducts: 837,
  totalValue: 284650.00,
  lowStockItems: 12,
  outOfStockItems: 3,
  pendingOrders: 8,
  recentMovements: 47
};

export const mockCategories: Category[] = [
  { id: '1', name: 'Electronics', productCount: 245, icon: '💻' },
  { id: '2', name: 'Furniture', productCount: 89, icon: '🪑' },
  { id: '3', name: 'Accessories', productCount: 312, icon: '🎧' },
  { id: '4', name: 'Office Supplies', productCount: 156, icon: '📎' },
  { id: '5', name: 'Storage', productCount: 35, icon: '📦' }
];

export const mockMovements: StockMovement[] = [
  {
    id: '1',
    productId: '1',
    type: 'in',
    quantity: 50,
    reason: 'Regular restock',
    timestamp: new Date('2026-02-14T10:30:00'),
    userId: 'user-1'
  },
  {
    id: '2',
    productId: '5',
    type: 'out',
    quantity: 25,
    reason: 'Customer order #4521',
    timestamp: new Date('2026-02-14T09:15:00'),
    userId: 'user-2'
  },
  {
    id: '3',
    productId: '2',
    type: 'out',
    quantity: 100,
    reason: 'Bulk order - Corporate',
    timestamp: new Date('2026-02-13T16:45:00'),
    userId: 'user-1'
  },
  {
    id: '4',
    productId: '7',
    type: 'adjustment',
    quantity: -3,
    reason: 'Inventory audit correction',
    timestamp: new Date('2026-02-13T14:20:00'),
    userId: 'user-3'
  }
];
