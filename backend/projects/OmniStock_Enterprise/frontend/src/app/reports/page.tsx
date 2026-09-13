'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

const reports = [
  { id: 1, name: 'Inventory Valuation', description: 'Total value of current stock across all warehouses', lastRun: '2026-02-14', icon: '📊' },
  { id: 2, name: 'Stock Movement', description: 'All stock movements for the selected period', lastRun: '2026-02-14', icon: '📈' },
  { id: 3, name: 'Low Stock Report', description: 'Products below minimum threshold levels', lastRun: '2026-02-14', icon: '⚠️' },
  { id: 4, name: 'Supplier Performance', description: 'Lead times, order accuracy, and reliability scores', lastRun: '2026-02-13', icon: '🏆' },
  { id: 5, name: 'Sales by Product', description: 'Revenue breakdown by product category', lastRun: '2026-02-13', icon: '💰' },
  { id: 6, name: 'Warehouse Utilization', description: 'Capacity usage across all locations', lastRun: '2026-02-12', icon: '🏭' },
];

const scheduledReports = [
  { name: 'Weekly Inventory Summary', schedule: 'Every Monday 9:00 AM', recipients: 3 },
  { name: 'Daily Stock Alerts', schedule: 'Daily 7:00 AM', recipients: 5 },
  { name: 'Monthly Performance', schedule: '1st of month', recipients: 8 },
];

export default function ReportsPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Reports</h1>
          <p className="text-gray-500 mt-1">Generate and schedule inventory reports</p>
        </div>
        <div className="flex gap-3">
          <Button variant="secondary">Schedule Report</Button>
          <Button>+ Custom Report</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-4">
          <h2 className="font-semibold text-gray-900">Available Reports</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {reports.map((report) => (
              <Card key={report.id} hover className="cursor-pointer">
                <CardContent className="p-4">
                  <div className="flex items-start gap-3">
                    <span className="text-2xl">{report.icon}</span>
                    <div className="flex-1">
                      <h3 className="font-semibold text-gray-900">{report.name}</h3>
                      <p className="text-sm text-gray-500 mt-1">{report.description}</p>
                      <div className="flex items-center justify-between mt-3">
                        <span className="text-xs text-gray-400">Last run: {report.lastRun}</span>
                        <Button variant="secondary" size="sm">Generate</Button>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          <h2 className="font-semibold text-gray-900">Scheduled Reports</h2>
          <Card>
            <CardContent className="p-0 divide-y divide-gray-100">
              {scheduledReports.map((report, i) => (
                <div key={i} className="p-4">
                  <h4 className="font-medium text-gray-900">{report.name}</h4>
                  <p className="text-sm text-gray-500 mt-1">{report.schedule}</p>
                  <div className="flex items-center justify-between mt-2">
                    <span className="text-xs text-gray-400">{report.recipients} recipients</span>
                    <Button variant="ghost" size="sm">Edit</Button>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card className="bg-indigo-50 border-indigo-200">
            <CardContent className="p-4">
              <h4 className="font-semibold text-indigo-900">Quick Export</h4>
              <p className="text-sm text-indigo-700 mt-1">Download current inventory data</p>
              <div className="flex gap-2 mt-3">
                <Button variant="secondary" size="sm">CSV</Button>
                <Button variant="secondary" size="sm">Excel</Button>
                <Button variant="secondary" size="sm">PDF</Button>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
