'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

const orders = [
  { id: 'ORD-2026-001', customer: 'Acme Corp', items: 5, total: 12500, status: 'pending', date: '2026-02-14' },
  { id: 'ORD-2026-002', customer: 'TechStart Inc', items: 3, total: 8900, status: 'shipped', date: '2026-02-13' },
  { id: 'ORD-2026-003', customer: 'DataFlow Systems', items: 12, total: 24300, status: 'delivered', date: '2026-02-12' },
  { id: 'ORD-2026-004', customer: 'CloudNet Solutions', items: 2, total: 5670, status: 'pending', date: '2026-02-14' },
  { id: 'ORD-2026-005', customer: 'InfoSys Ltd', items: 8, total: 15800, status: 'processing', date: '2026-02-13' },
  { id: 'ORD-2026-006', customer: 'Digital Wave', items: 4, total: 7200, status: 'shipped', date: '2026-02-11' },
];

const statusConfig: Record<string, { label: string; variant: 'default' | 'success' | 'warning' | 'error' | 'info' }> = {
  pending: { label: 'Pending', variant: 'warning' },
  processing: { label: 'Processing', variant: 'info' },
  shipped: { label: 'Shipped', variant: 'info' },
  delivered: { label: 'Delivered', variant: 'success' },
  cancelled: { label: 'Cancelled', variant: 'error' },
};

export default function OrdersPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Orders</h1>
          <p className="text-gray-500 mt-1">Track and manage customer orders</p>
        </div>
        <div className="flex gap-3">
          <Button variant="secondary">Export</Button>
          <Button>+ New Order</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="p-4">
          <p className="text-sm text-gray-500">Total Orders</p>
          <p className="text-2xl font-bold text-gray-900">156</p>
        </Card>
        <Card className="p-4">
          <p className="text-sm text-gray-500">Pending</p>
          <p className="text-2xl font-bold text-amber-600">8</p>
        </Card>
        <Card className="p-4">
          <p className="text-sm text-gray-500">Shipped</p>
          <p className="text-2xl font-bold text-blue-600">23</p>
        </Card>
        <Card className="p-4">
          <p className="text-sm text-gray-500">Revenue (MTD)</p>
          <p className="text-2xl font-bold text-emerald-600">$284K</p>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent Orders</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50">
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Order ID</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Customer</th>
                <th className="px-4 py-3 text-center text-xs font-semibold text-gray-500 uppercase">Items</th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase">Total</th>
                <th className="px-4 py-3 text-center text-xs font-semibold text-gray-500 uppercase">Status</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Date</th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {orders.map((order) => (
                <tr key={order.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono text-sm font-medium text-indigo-600">{order.id}</td>
                  <td className="px-4 py-3 font-medium text-gray-900">{order.customer}</td>
                  <td className="px-4 py-3 text-center text-gray-600">{order.items}</td>
                  <td className="px-4 py-3 text-right font-semibold text-gray-900">${order.total.toLocaleString()}</td>
                  <td className="px-4 py-3 text-center">
                    <Badge variant={statusConfig[order.status].variant}>
                      {statusConfig[order.status].label}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-gray-500">{order.date}</td>
                  <td className="px-4 py-3 text-right">
                    <Button variant="ghost" size="sm">View</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
