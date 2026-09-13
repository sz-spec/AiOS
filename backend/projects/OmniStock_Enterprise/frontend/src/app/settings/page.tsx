'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

export default function SettingsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-gray-500 mt-1">Manage your OmniStock configuration</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          {/* General Settings */}
          <Card>
            <CardHeader>
              <CardTitle>General</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Company Name</label>
                <input type="text" defaultValue="OmniStock Demo Corp" className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500" />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Currency</label>
                  <select className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500">
                    <option>USD ($)</option>
                    <option>EUR (€)</option>
                    <option>GBP (£)</option>
                    <option>ILS (₪)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Timezone</label>
                  <select className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500">
                    <option>America/Los_Angeles (PST)</option>
                    <option>America/New_York (EST)</option>
                    <option>Europe/London (GMT)</option>
                    <option>Asia/Jerusalem (IST)</option>
                  </select>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Inventory Settings */}
          <Card>
            <CardHeader>
              <CardTitle>Inventory Rules</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Default Low Stock Threshold</label>
                  <input type="number" defaultValue="20" className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Reorder Point Multiplier</label>
                  <input type="number" defaultValue="1.5" step="0.1" className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500" />
                </div>
              </div>
              <div className="flex items-center gap-3">
                <input type="checkbox" id="autoReorder" defaultChecked className="w-4 h-4 text-indigo-600 rounded" />
                <label htmlFor="autoReorder" className="text-sm text-gray-700">Enable automatic reorder suggestions</label>
              </div>
              <div className="flex items-center gap-3">
                <input type="checkbox" id="lowStockAlerts" defaultChecked className="w-4 h-4 text-indigo-600 rounded" />
                <label htmlFor="lowStockAlerts" className="text-sm text-gray-700">Send low stock email alerts</label>
              </div>
            </CardContent>
          </Card>

          {/* Notifications */}
          <Card>
            <CardHeader>
              <CardTitle>Notifications</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {[
                { label: 'Low stock alerts', enabled: true },
                { label: 'Out of stock alerts', enabled: true },
                { label: 'New order notifications', enabled: true },
                { label: 'Weekly summary reports', enabled: false },
                { label: 'Supplier updates', enabled: false },
              ].map((item, i) => (
                <div key={i} className="flex items-center justify-between py-2">
                  <span className="text-gray-700">{item.label}</span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input type="checkbox" defaultChecked={item.enabled} className="sr-only peer" />
                    <div className="w-11 h-6 bg-gray-200 peer-focus:ring-2 peer-focus:ring-indigo-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-indigo-600"></div>
                  </label>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>

        {/* Sidebar */}
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Account</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center gap-4">
                <div className="w-16 h-16 bg-indigo-100 rounded-full flex items-center justify-center text-xl font-bold text-indigo-600">
                  JD
                </div>
                <div>
                  <p className="font-semibold text-gray-900">John Doe</p>
                  <p className="text-sm text-gray-500">admin@omnistock.demo</p>
                  <p className="text-xs text-indigo-600">Administrator</p>
                </div>
              </div>
              <Button variant="secondary" className="w-full">Edit Profile</Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Plan</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between mb-4">
                <span className="font-semibold text-gray-900">Enterprise</span>
                <span className="px-2 py-1 bg-emerald-100 text-emerald-700 text-xs font-bold rounded-full">ACTIVE</span>
              </div>
              <ul className="space-y-2 text-sm text-gray-600 mb-4">
                <li className="flex items-center gap-2">
                  <span className="text-emerald-500">✓</span> Unlimited products
                </li>
                <li className="flex items-center gap-2">
                  <span className="text-emerald-500">✓</span> Unlimited warehouses
                </li>
                <li className="flex items-center gap-2">
                  <span className="text-emerald-500">✓</span> Advanced analytics
                </li>
                <li className="flex items-center gap-2">
                  <span className="text-emerald-500">✓</span> API access
                </li>
              </ul>
              <Button variant="secondary" className="w-full">Manage Billing</Button>
            </CardContent>
          </Card>

          <Card className="bg-red-50 border-red-200">
            <CardContent className="p-4">
              <h4 className="font-semibold text-red-900">Danger Zone</h4>
              <p className="text-sm text-red-700 mt-1 mb-3">Permanently delete your account and all data</p>
              <Button variant="danger" size="sm">Delete Account</Button>
            </CardContent>
          </Card>
        </div>
      </div>

      <div className="flex justify-end gap-3 pt-4 border-t border-gray-200">
        <Button variant="secondary">Cancel</Button>
        <Button>Save Changes</Button>
      </div>
    </div>
  );
}
