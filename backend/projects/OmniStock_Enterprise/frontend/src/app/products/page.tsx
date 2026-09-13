'use client';

import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { StockStatusBadge } from '@/components/ui/badge';
import { mockProducts } from '@/lib/mock-data';

export default function ProductsPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Products</h1>
          <p className="text-gray-500 mt-1">Manage your product catalog</p>
        </div>
        <div className="flex gap-3">
          <Button variant="secondary">Import</Button>
          <Button>+ Add Product</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {mockProducts.map((product) => (
          <Card key={product.id} hover className="overflow-hidden">
            <div className="h-32 bg-gradient-to-br from-gray-100 to-gray-200 flex items-center justify-center">
              <span className="text-4xl">📦</span>
            </div>
            <CardContent className="p-4">
              <div className="flex items-start justify-between mb-2">
                <span className="font-mono text-xs text-gray-500">{product.sku}</span>
                <StockStatusBadge status={product.status} />
              </div>
              <h3 className="font-semibold text-gray-900 mb-1 line-clamp-1">{product.name}</h3>
              <p className="text-sm text-gray-500 mb-3">{product.category}</p>
              <div className="flex items-center justify-between">
                <span className="text-lg font-bold text-gray-900">${product.price}</span>
                <span className="text-sm text-gray-500">{product.quantity} in stock</span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
