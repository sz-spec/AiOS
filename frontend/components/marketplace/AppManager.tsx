'use client';

import { useState, useEffect } from 'react';
import { useMarketplace, InstalledApp } from '@/hooks/useMarketplace';

// ───────────── Icons ─────────────

function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

function PackageIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="16.5" y1="9.4" x2="7.5" y2="4.21" />
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
      <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
      <line x1="12" y1="22.08" x2="12" y2="12" />
    </svg>
  );
}

// ───────────── Sub-components ─────────────

function Toggle({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: (val: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 border-transparent transition-colors cursor-pointer ${
        disabled ? 'opacity-50 cursor-not-allowed' : ''
      } ${checked ? 'bg-green-600' : 'bg-white/20'}`}
    >
      <span
        className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
          checked ? 'translate-x-4' : 'translate-x-0'
        }`}
      />
    </button>
  );
}

function ConfirmDialog({
  appName,
  onConfirm,
  onCancel,
}: {
  appName: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-[#1a1a1a] border border-white/10 rounded-xl p-6 max-w-sm w-full mx-4">
        <h3 className="text-base font-semibold text-white mb-2">Uninstall App</h3>
        <p className="text-sm text-gray-400 mb-6">
          Are you sure you want to uninstall <strong className="text-white">{appName}</strong>?
          This action cannot be undone.
        </p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg text-sm text-gray-400 hover:text-white bg-white/5 hover:bg-white/10 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 rounded-lg text-sm text-white bg-red-600 hover:bg-red-700 transition-colors"
          >
            Uninstall
          </button>
        </div>
      </div>
    </div>
  );
}

// ───────────── Component ─────────────

export default function AppManager() {
  const { installedApps, isLoading, error, getInstalled, toggleApp, uninstall } = useMarketplace();
  const [confirmUninstall, setConfirmUninstall] = useState<InstalledApp | null>(null);
  const [togglingSlug, setTogglingSlug] = useState<string | null>(null);

  useEffect(() => {
    getInstalled();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleToggle = async (slug: string, enabled: boolean) => {
    setTogglingSlug(slug);
    await toggleApp(slug, enabled);
    setTogglingSlug(null);
  };

  const handleUninstall = async () => {
    if (!confirmUninstall) return;
    await uninstall(confirmUninstall.slug);
    setConfirmUninstall(null);
  };

  return (
    <div className="p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <span className="text-gray-400">
            <PackageIcon />
          </span>
          <h2 className="text-lg font-semibold text-white">Installed Apps</h2>
          <span className="text-xs text-gray-500 bg-white/5 px-2 py-0.5 rounded-full">
            {installedApps.length}
          </span>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-4 p-3 rounded-lg bg-red-900/30 border border-red-800 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Loading */}
      {isLoading && installedApps.length === 0 ? (
        <div className="flex items-center justify-center py-16">
          <div className="flex gap-2">
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className="w-2 h-2 rounded-full bg-white/40 animate-pulse"
                style={{ animationDelay: `${i * 0.2}s` }}
              />
            ))}
          </div>
        </div>
      ) : installedApps.length === 0 ? (
        <div className="text-center py-16 text-gray-500 border border-white/5 rounded-xl">
          <p className="text-lg mb-1">No apps installed</p>
          <p className="text-sm">Browse the marketplace to discover apps</p>
        </div>
      ) : (
        <div className="space-y-2">
          {installedApps.map((app) => (
            <div
              key={app.slug}
              className="flex items-center gap-4 p-4 rounded-xl border border-white/10 bg-white/[0.02] hover:bg-white/[0.04] transition-colors"
            >
              {/* Icon */}
              <div className="w-10 h-10 rounded-xl bg-white/10 flex items-center justify-center text-lg shrink-0">
                {app.icon || app.name.charAt(0)}
              </div>

              {/* Info */}
              <div className="flex-1 min-w-0">
                <h3 className="text-sm font-medium text-white truncate">{app.name}</h3>
                <p className="text-xs text-gray-500">
                  v{app.version} &middot; Installed {new Date(app.installed_at).toLocaleDateString()}
                </p>
              </div>

              {/* Toggle */}
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-500">
                  {app.enabled ? 'Enabled' : 'Disabled'}
                </span>
                <Toggle
                  checked={app.enabled}
                  onChange={(val) => handleToggle(app.slug, val)}
                  disabled={togglingSlug === app.slug}
                />
              </div>

              {/* Uninstall */}
              <button
                onClick={() => setConfirmUninstall(app)}
                className="p-2 rounded-lg text-gray-500 hover:text-red-400 hover:bg-red-900/20 transition-colors"
                title="Uninstall"
              >
                <TrashIcon />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Confirm dialog */}
      {confirmUninstall && (
        <ConfirmDialog
          appName={confirmUninstall.name}
          onConfirm={handleUninstall}
          onCancel={() => setConfirmUninstall(null)}
        />
      )}
    </div>
  );
}
