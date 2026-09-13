// OmniStock Enterprise - Product Table Component
'use client';

import { useState } from 'react';
import { Product } from '@/types/inventory';
import { StockStatusBadge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface ProductTableProps {
  products: Product[];
  onEdit?: (product: Product) => void;
  onRestock?: (product: Product) => void;
}

function SortIcon({ field, sortField, sortDirection }: {
  field: keyof Product;
  sortField: keyof Product;
  sortDirection: 'asc' | 'desc';
}) {
  if (sortField !== field) return <span className="text-gray-300 ml-1">↕</span>;
  return <span className="ml-1">{sortDirection === 'asc' ? '↑' : '↓'}</span>;
}

export function ProductTable({ products, onEdit, onRestock }: ProductTableProps) {
  const [sortField, setSortField] = useState<keyof Product>('name');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');

  const sortedProducts = [...products].sort((a, b) => {
    const aVal = a[sortField];
    const bVal = b[sortField];
    const modifier = sortDirection === 'asc' ? 1 : -1;

    if (typeof aVal === 'string' && typeof bVal === 'string') {
      return aVal.localeCompare(bVal) * modifier;
    }
    if (typeof aVal === 'number' && typeof bVal === 'number') {
      return (aVal - bVal) * modifier;
    }
    return 0;
  });

  const handleSort = (field: keyof Product) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
  };



  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="border-b border-gray-200">
            <th
              className="px-4 py-3 text-left text-sm font-semibold text-gray-600 cursor-pointer hover:text-gray-900"
              onClick={() => handleSort('sku')}
            >
              SKU <SortIcon sortField={sortField} sortDirection={sortDirection} field="sku" />
            </th>
            <th
              className="px-4 py-3 text-left text-sm font-semibold text-gray-600 cursor-pointer hover:text-gray-900"
              onClick={() => handleSort('name')}
            >
              Product <SortIcon sortField={sortField} sortDirection={sortDirection} field="name" />
            </th>
            <th
              className="px-4 py-3 text-left text-sm font-semibold text-gray-600 cursor-pointer hover:text-gray-900"
              onClick={() => handleSort('category')}
            >
              Category <SortIcon sortField={sortField} sortDirection={sortDirection} field="category" />
            </th>
            <th
              className="px-4 py-3 text-right text-sm font-semibold text-gray-600 cursor-pointer hover:text-gray-900"
              onClick={() => handleSort('quantity')}
            >
              Qty <SortIcon sortField={sortField} sortDirection={sortDirection} field="quantity" />
            </th>
            <th
              className="px-4 py-3 text-right text-sm font-semibold text-gray-600 cursor-pointer hover:text-gray-900"
              onClick={() => handleSort('price')}
            >
              Price <SortIcon sortField={sortField} sortDirection={sortDirection} field="price" />
            </th>
            <th className="px-4 py-3 text-center text-sm font-semibold text-gray-600">
              Status
            </th>
            <th className="px-4 py-3 text-left text-sm font-semibold text-gray-600">
              Location
            </th>
            <th className="px-4 py-3 text-right text-sm font-semibold text-gray-600">
              Actions
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {sortedProducts.map((product) => (
            <tr
              key={product.id}
              className="hover:bg-gray-50 transition-colors"
            >
              <td className="px-4 py-3">
                <span className="font-mono text-sm text-gray-600">{product.sku}</span>
              </td>
              <td className="px-4 py-3">
                <span className="font-medium text-gray-900">{product.name}</span>
              </td>
              <td className="px-4 py-3 text-gray-600">
                {product.category}
              </td>
              <td className="px-4 py-3 text-right">
                <span className={`font-semibold ${
                  product.quantity === 0 ? 'text-red-600' :
                  product.quantity < product.minStock ? 'text-amber-600' :
                  'text-gray-900'
                }`}>
                  {product.quantity}
                </span>
                <span className="text-gray-400 text-sm ml-1">
                  / {product.maxStock}
                </span>
              </td>
              <td className="px-4 py-3 text-right font-medium text-gray-900">
                ${product.price.toFixed(2)}
              </td>
              <td className="px-4 py-3 text-center">
                <StockStatusBadge status={product.status} />
              </td>
              <td className="px-4 py-3 text-sm text-gray-500">
                {product.location}
              </td>
              <td className="px-4 py-3 text-right space-x-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onEdit?.(product)}
                >
                  Edit
                </Button>
                {product.status !== 'overstock' && (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => onRestock?.(product)}
                  >
                    Restock
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
