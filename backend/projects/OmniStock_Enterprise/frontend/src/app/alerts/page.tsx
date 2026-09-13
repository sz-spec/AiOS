'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

const alerts = [
  { id: 1, type: 'out_of_stock', severity: 'critical', message: 'Server Rack 42U is out of stock in San Jose DC', product: 'SRV-088', time: '2 hours ago', status: 'unresolved' },
  { id: 2, type: 'low_stock', severity: 'high', message: 'Fiber Optic Cable 100m running low in Austin - only 12 units left', product: 'CBL-202', time: '4 hours ago', status: 'unresolved' },
  { id: 3, type: 'out_of_stock', severity: 'high', message: 'SFP+ Transceiver out of stock in Austin', product: 'NET-201', time: '5 hours ago', status: 'unresolved' },
  { id: 4, type: 'low_stock', severity: 'medium', message: 'Rack Mount PDU below minimum threshold in San Jose', product: 'PWR-056', time: '1 day ago', status: 'acknowledged' },
  { id: 5, type: 'overstock', severity: 'low', message: 'UPS Battery Backup exceeds max capacity in Austin (234/100)', product: 'PWR-112', time: '1 day ago', status: 'resolved' },
  { id: 6, type: 'reorder', severity: 'medium', message: 'Automatic reorder triggered for Industrial Router V3', product: 'NET-001', time: '2 days ago', status: 'resolved' },
];

const severityConfig: Record<string, { color: string; bg: string }> = {
  critical: { color: 'text-red-700', bg: 'bg-red-100' },
  high: { color: 'text-orange-700', bg: 'bg-orange-100' },
  medium: { color: 'text-amber-700', bg: 'bg-amber-100' },
  low: { color: 'text-blue-700', bg: 'bg-blue-100' },
};

const statusConfig: Record<string, { label: string; variant: 'default' | 'success' | 'warning' | 'error' }> = {
  unresolved: { label: 'Unresolved', variant: 'error' },
  acknowledged: { label: 'Acknowledged', variant: 'warning' },
  resolved: { label: 'Resolved', variant: 'success' },
};

export default function AlertsPage() {
  const unresolvedCount = alerts.filter(a => a.status === 'unresolved').length;
  const criticalCount = alerts.filter(a => a.severity === 'critical').length;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Alerts</h1>
          <p className="text-gray-500 mt-1">Monitor stock alerts and notifications</p>
        </div>
        <div className="flex gap-3">
          <Button variant="secondary">Mark All Read</Button>
          <Button variant="secondary">Configure Rules</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="p-4 border-l-4 border-l-red-500">
          <p className="text-sm text-gray-500">Critical</p>
          <p className="text-2xl font-bold text-red-600">{criticalCount}</p>
        </Card>
        <Card className="p-4 border-l-4 border-l-orange-500">
          <p className="text-sm text-gray-500">Unresolved</p>
          <p className="text-2xl font-bold text-orange-600">{unresolvedCount}</p>
        </Card>
        <Card className="p-4 border-l-4 border-l-amber-500">
          <p className="text-sm text-gray-500">Acknowledged</p>
          <p className="text-2xl font-bold text-amber-600">1</p>
        </Card>
        <Card className="p-4 border-l-4 border-l-emerald-500">
          <p className="text-sm text-gray-500">Resolved Today</p>
          <p className="text-2xl font-bold text-emerald-600">2</p>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>All Alerts</CardTitle>
          <select className="px-3 py-1.5 text-sm border border-gray-200 rounded-lg">
            <option>All Statuses</option>
            <option>Unresolved</option>
            <option>Acknowledged</option>
            <option>Resolved</option>
          </select>
        </CardHeader>
        <CardContent className="p-0">
          <div className="divide-y divide-gray-100">
            {alerts.map((alert) => (
              <div key={alert.id} className="p-4 hover:bg-gray-50 flex items-start gap-4">
                <div className={`p-2 rounded-lg ${severityConfig[alert.severity].bg}`}>
                  <svg className={`w-5 h-5 ${severityConfig[alert.severity].color}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    {alert.severity === 'critical' || alert.severity === 'high' ? (
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                    ) : (
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    )}
                  </svg>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-xs font-bold uppercase ${severityConfig[alert.severity].color}`}>
                      {alert.severity}
                    </span>
                    <span className="text-gray-300">•</span>
                    <span className="font-mono text-xs text-gray-500">{alert.product}</span>
                  </div>
                  <p className="text-gray-900 font-medium">{alert.message}</p>
                  <p className="text-sm text-gray-500 mt-1">{alert.time}</p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant={statusConfig[alert.status].variant}>
                    {statusConfig[alert.status].label}
                  </Badge>
                  {alert.status === 'unresolved' && (
                    <Button variant="secondary" size="sm">Resolve</Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
