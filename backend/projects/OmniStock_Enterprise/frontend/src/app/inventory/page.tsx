'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { StatsCard } from '@/components/inventory/stats-card';
import { StockStatusBadge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

const inventoryData = [
  { name: 'Industrial Router V3', sku: 'NET-001', warehouse: 'San Jose', stock: 142, maxStock: 200, status: 'in_stock' as const },
  { name: 'Fiber Optic Cable 100m', sku: 'CBL-202', warehouse: 'Austin', stock: 12, maxStock: 100, status: 'low_stock' as const },
  { name: 'Server Rack 42U', sku: 'SRV-088', warehouse: 'San Jose', stock: 0, maxStock: 20, status: 'out_of_stock' as const },
  { name: 'Ethernet Switch 48-Port', sku: 'NET-045', warehouse: 'Seattle', stock: 89, maxStock: 150, status: 'in_stock' as const },
  { name: 'UPS Battery Backup', sku: 'PWR-112', warehouse: 'Austin', stock: 234, maxStock: 100, status: 'overstock' as const },
  { name: 'Cat6 Patch Panel', sku: 'CBL-089', warehouse: 'Seattle', stock: 45, maxStock: 80, status: 'in_stock' as const },
  { name: 'Rack Mount PDU', sku: 'PWR-056', warehouse: 'San Jose', stock: 8, maxStock: 50, status: 'low_stock' as const },
  { name: 'SFP+ Transceiver', sku: 'NET-201', warehouse: 'Austin', stock: 0, maxStock: 200, status: 'out_of_stock' as const },
];

const warehouseStats = [
  { name: 'San Jose', items: 1245, value: '$2.4M', utilization: 78 },
  { name: 'Austin', items: 892, value: '$1.8M', utilization: 65 },
  { name: 'Seattle', items: 567, value: '$980K', utilization: 45 },
];

export default function InventoryPage() {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Inventory Management</h1>
          <p className="text-gray-500 mt-1">Real-time stock levels and warehouse distribution</p>
        </div>
        <div className="flex gap-3">
          <Button variant="secondary">Import CSV</Button>
          <Button variant="secondary">Export</Button>
          <Button>+ Add Item</Button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatsCard
          title="Total SKUs"
          value="2,704"
          subtitle="Across 3 warehouses"
          icon={
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
            </svg>
          }
          trend={{ value: 5.2, isPositive: true }}
        />
        <StatsCard
          title="Total Units"
          value="48,291"
          subtitle="In stock"
          icon={
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
            </svg>
          }
          variant="success"
        />
        <StatsCard
          title="Low Stock Alerts"
          value="23"
          subtitle="Need attention"
          icon={
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          }
          variant="warning"
        />
        <StatsCard
          title="Out of Stock"
          value="8"
          subtitle="Critical items"
          icon={
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
            </svg>
          }
          variant="danger"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Inventory Table */}
        <Card className="lg:col-span-2">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Stock Levels</CardTitle>
            <div className="flex gap-2">
              <input
                type="text"
                placeholder="Search SKU..."
                className="px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              <select className="px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500">
                <option>All Warehouses</option>
                <option>San Jose</option>
                <option>Austin</option>
                <option>Seattle</option>
              </select>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-gray-200 bg-gray-50">
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Product</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">SKU</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">Warehouse</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">Stock</th>
                    <th className="px-4 py-3 text-center text-xs font-semibold text-gray-500 uppercase tracking-wider">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {inventoryData.map((item, i) => (
                    <tr key={i} className="hover:bg-gray-50 transition-colors">
                      <td className="px-4 py-3 font-medium text-gray-900">{item.name}</td>
                      <td className="px-4 py-3 font-mono text-sm text-gray-600">{item.sku}</td>
                      <td className="px-4 py-3 text-gray-600">{item.warehouse}</td>
                      <td className="px-4 py-3 text-right">
                        <span className={`font-semibold ${
                          item.stock === 0 ? 'text-red-600' :
                          item.stock < item.maxStock * 0.2 ? 'text-amber-600' :
                          'text-gray-900'
                        }`}>
                          {item.stock}
                        </span>
                        <span className="text-gray-400 text-sm">/{item.maxStock}</span>
                      </td>
                      <td className="px-4 py-3 text-center">
                        <StockStatusBadge status={item.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        {/* Warehouse Distribution */}
        <Card>
          <CardHeader>
            <CardTitle>Warehouse Distribution</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {warehouseStats.map((wh) => (
              <div key={wh.name} className="p-4 rounded-lg bg-gray-50 hover:bg-gray-100 transition-colors cursor-pointer">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-semibold text-gray-900">{wh.name}</span>
                  <span className="text-sm font-medium text-indigo-600">{wh.value}</span>
                </div>
                <div className="flex items-center justify-between text-sm text-gray-500 mb-2">
                  <span>{wh.items.toLocaleString()} items</span>
                  <span>{wh.utilization}% capacity</span>
                </div>
                <div className="w-full bg-gray-200 rounded-full h-2">
                  <div
                    className={`h-2 rounded-full transition-all ${
                      wh.utilization > 80 ? 'bg-red-500' :
                      wh.utilization > 60 ? 'bg-amber-500' :
                      'bg-emerald-500'
                    }`}
                    style={{ width: `${wh.utilization}%` }}
                  />
                </div>
              </div>
            ))}

            <div className="pt-4 border-t border-gray-200">
              <h4 className="text-sm font-semibold text-gray-900 mb-3">Quick Actions</h4>
              <div className="space-y-2">
                <Button variant="secondary" className="w-full justify-start">
                  <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  Transfer Stock
                </Button>
                <Button variant="secondary" className="w-full justify-start">
                  <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                  </svg>
                  Inventory Audit
                </Button>
                <Button variant="secondary" className="w-full justify-start">
                  <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  Reorder Report
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
