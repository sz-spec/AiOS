// OmniStock Enterprise - Inventory Types

export interface Product {
  id: string;
  sku: string;
  name: string;
  category: string;
  quantity: number;
  minStock: number;
  maxStock: number;
  price: number;
  cost: number;
  supplier: string;
  location: string;
  lastRestocked: Date;
  status: StockStatus;
}

export type StockStatus = 'in_stock' | 'low_stock' | 'out_of_stock' | 'overstock';

export interface StockMovement {
  id: string;
  productId: string;
  type: 'in' | 'out' | 'adjustment';
  quantity: number;
  reason: string;
  timestamp: Date;
  userId: string;
}

export interface Supplier {
  id: string;
  name: string;
  email: string;
  phone: string;
  address: string;
  leadTimeDays: number;
}

export interface DashboardStats {
  totalProducts: number;
  totalValue: number;
  lowStockItems: number;
  outOfStockItems: number;
  pendingOrders: number;
  recentMovements: number;
}

export interface Category {
  id: string;
  name: string;
  productCount: number;
  icon: string;
}
