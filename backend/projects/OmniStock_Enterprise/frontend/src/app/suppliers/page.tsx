'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

const suppliers = [
  { id: 1, name: 'Global Supplies Inc', contact: 'Mike Johnson', email: 'sales@globalsupplies.com', phone: '+1-555-0100', products: 45, leadTime: 7, status: 'active', rating: 4.8 },
  { id: 2, name: 'TechParts Direct', contact: 'Lisa Chen', email: 'orders@techpartsdirect.com', phone: '+1-555-0200', products: 32, leadTime: 14, status: 'active', rating: 4.5 },
  { id: 3, name: 'NetworkGear Pro', contact: 'David Park', email: 'wholesale@networkgearpro.com', phone: '+1-555-0300', products: 28, leadTime: 5, status: 'active', rating: 4.9 },
  { id: 4, name: 'Industrial Parts Co', contact: 'Sarah Williams', email: 'info@industrialparts.co', phone: '+1-555-0400', products: 56, leadTime: 10, status: 'active', rating: 4.2 },
  { id: 5, name: 'FastShip Electronics', contact: 'Tom Brown', email: 'orders@fastship.com', phone: '+1-555-0500', products: 18, leadTime: 3, status: 'inactive', rating: 3.8 },
];

export default function SuppliersPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Suppliers</h1>
          <p className="text-gray-500 mt-1">Manage your supplier relationships</p>
        </div>
        <div className="flex gap-3">
          <Button variant="secondary">Export List</Button>
          <Button>+ Add Supplier</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="p-4">
          <p className="text-sm text-gray-500">Total Suppliers</p>
          <p className="text-2xl font-bold text-gray-900">{suppliers.length}</p>
        </Card>
        <Card className="p-4">
          <p className="text-sm text-gray-500">Active</p>
          <p className="text-2xl font-bold text-emerald-600">{suppliers.filter(s => s.status === 'active').length}</p>
        </Card>
        <Card className="p-4">
          <p className="text-sm text-gray-500">Avg Lead Time</p>
          <p className="text-2xl font-bold text-blue-600">8 days</p>
        </Card>
        <Card className="p-4">
          <p className="text-sm text-gray-500">Products Supplied</p>
          <p className="text-2xl font-bold text-indigo-600">179</p>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {suppliers.map((supplier) => (
          <Card key={supplier.id} hover>
            <CardContent className="p-6">
              <div className="flex items-start justify-between mb-4">
                <div>
                  <h3 className="font-semibold text-lg text-gray-900">{supplier.name}</h3>
                  <p className="text-gray-500">{supplier.contact}</p>
                </div>
                <Badge variant={supplier.status === 'active' ? 'success' : 'default'}>
                  {supplier.status}
                </Badge>
              </div>

              <div className="grid grid-cols-2 gap-4 mb-4">
                <div>
                  <p className="text-xs text-gray-500 uppercase">Email</p>
                  <p className="text-sm text-gray-900">{supplier.email}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500 uppercase">Phone</p>
                  <p className="text-sm text-gray-900">{supplier.phone}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500 uppercase">Products</p>
                  <p className="text-sm font-semibold text-gray-900">{supplier.products}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500 uppercase">Lead Time</p>
                  <p className="text-sm font-semibold text-gray-900">{supplier.leadTime} days</p>
                </div>
              </div>

              <div className="flex items-center justify-between pt-4 border-t border-gray-100">
                <div className="flex items-center gap-1">
                  <span className="text-amber-500">★</span>
                  <span className="font-semibold text-gray-900">{supplier.rating}</span>
                  <span className="text-gray-500 text-sm">rating</span>
                </div>
                <div className="flex gap-2">
                  <Button variant="ghost" size="sm">View Orders</Button>
                  <Button variant="secondary" size="sm">Contact</Button>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
