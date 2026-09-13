// OmniStock Enterprise - Dashboard Component
'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatsCard } from './stats-card';
import { ProductTable } from './product-table';
import { Button } from '@/components/ui/button';
import { mockProducts, mockStats, mockCategories, mockMovements } from '@/lib/mock-data';
import { Product } from '@/types/inventory';

export function Dashboard() {
  const handleEdit = (product: Product) => {
    console.log('Edit product:', product);
  };

  const handleRestock = (product: Product) => {
    console.log('Restock product:', product);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="text-gray-500 mt-1">Welcome back! Here&apos;s your inventory overview.</p>
        </div>
        <div className="flex gap-3">
          <Button variant="secondary">Export Report</Button>
          <Button>+ Add Product</Button>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatsCard
          title="Total Products"
          value={mockStats.totalProducts.toLocaleString()}
          subtitle="Across all categories"
          icon={
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
            </svg>
          }
          trend={{ value: 12, isPositive: true }}
        />
        <StatsCard
          title="Total Value"
          value={`$${mockStats.totalValue.toLocaleString()}`}
          subtitle="Current inventory worth"
          icon={
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          }
          variant="success"
          trend={{ value: 8, isPositive: true }}
        />
        <StatsCard
          title="Low Stock Items"
          value={mockStats.lowStockItems}
          subtitle="Need restocking soon"
          icon={
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          }
          variant="warning"
        />
        <StatsCard
          title="Out of Stock"
          value={mockStats.outOfStockItems}
          subtitle="Immediate action needed"
          icon={
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
            </svg>
          }
          variant="danger"
        />
      </div>

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Product Table */}
        <Card className="lg:col-span-2">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Recent Products</CardTitle>
            <Button variant="ghost" size="sm">View All →</Button>
          </CardHeader>
          <CardContent className="p-0">
            <ProductTable
              products={mockProducts}
              onEdit={handleEdit}
              onRestock={handleRestock}
            />
          </CardContent>
        </Card>

        {/* Sidebar Cards */}
        <div className="space-y-6">
          {/* Categories */}
          <Card>
            <CardHeader>
              <CardTitle>Categories</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {mockCategories.map((category) => (
                <div
                  key={category.id}
                  className="flex items-center justify-between p-3 rounded-lg hover:bg-gray-50 cursor-pointer transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">{category.icon}</span>
                    <span className="font-medium text-gray-900">{category.name}</span>
                  </div>
                  <span className="text-sm text-gray-500">{category.productCount}</span>
                </div>
              ))}
            </CardContent>
          </Card>

          {/* Recent Activity */}
          <Card>
            <CardHeader>
              <CardTitle>Recent Activity</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {mockMovements.slice(0, 4).map((movement) => {
                const product = mockProducts.find(p => p.id === movement.productId);
                return (
                  <div key={movement.id} className="flex items-start gap-3">
                    <div className={`
                      w-8 h-8 rounded-full flex items-center justify-center text-sm
                      ${movement.type === 'in' ? 'bg-emerald-100 text-emerald-600' :
                        movement.type === 'out' ? 'bg-red-100 text-red-600' :
                        'bg-gray-100 text-gray-600'}
                    `}>
                      {movement.type === 'in' ? '↑' : movement.type === 'out' ? '↓' : '~'}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 truncate">
                        {product?.name || 'Unknown Product'}
                      </p>
                      <p className="text-xs text-gray-500">{movement.reason}</p>
                    </div>
                    <span className={`text-sm font-semibold ${
                      movement.type === 'in' ? 'text-emerald-600' :
                      movement.type === 'out' ? 'text-red-600' :
                      'text-gray-600'
                    }`}>
                      {movement.type === 'in' ? '+' : ''}{movement.quantity}
                    </span>
                  </div>
                );
              })}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
