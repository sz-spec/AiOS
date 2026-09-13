'use client';

import { useState, useEffect } from 'react';
import { useDeveloper, DeveloperApp } from '@/hooks/useDeveloper';

// ───────────── Icons ─────────────

function DollarIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="1" x2="12" y2="23" />
      <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
    </svg>
  );
}

function ClockIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function StarIcon({ filled }: { filled: boolean }) {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill={filled ? '#f59e0b' : 'none'} stroke="#f59e0b" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
    </svg>
  );
}

// ───────────── Sub-components ─────────────

function RevenueCard({
  title,
  amount,
  icon,
  subtitle,
}: {
  title: string;
  amount: number;
  icon: React.ReactNode;
  subtitle?: string;
}) {
  return (
    <div className="p-5 rounded-xl border border-white/10 bg-white/[0.03]">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">{title}</span>
        <span className="text-gray-500">{icon}</span>
      </div>
      <p className="text-2xl font-bold text-white">${amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</p>
      {subtitle && <p className="text-xs text-gray-500 mt-1">{subtitle}</p>}
    </div>
  );
}

function StatusBadge({ status }: { status: DeveloperApp['status'] }) {
  const colors: Record<string, string> = {
    draft: 'bg-gray-800 text-gray-400 border-gray-700',
    in_review: 'bg-amber-900/40 text-amber-400 border-amber-800',
    approved: 'bg-blue-900/40 text-blue-400 border-blue-800',
    rejected: 'bg-red-900/40 text-red-400 border-red-800',
    published: 'bg-green-900/40 text-green-400 border-green-800',
  };

  const label: Record<string, string> = {
    draft: 'Draft',
    in_review: 'In Review',
    approved: 'Approved',
    rejected: 'Rejected',
    published: 'Published',
  };

  return (
    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${colors[status] ?? colors.draft}`}>
      {label[status] ?? status}
    </span>
  );
}

function MiniRating({ rating }: { rating: number }) {
  return (
    <span className="flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((s) => (
        <StarIcon key={s} filled={s <= Math.round(rating)} />
      ))}
      <span className="ml-1 text-xs text-gray-400">{rating.toFixed(1)}</span>
    </span>
  );
}

// ───────────── Page ─────────────

export default function DeveloperPage() {
  const {
    profile,
    apps,
    earnings,
    isLoading,
    error,
    getProfile,
    getApps,
    getEarnings,
  } = useDeveloper();

  const [showSubmitHint, setShowSubmitHint] = useState(false);

  useEffect(() => {
    getProfile();
    getApps();
    getEarnings();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Developer Dashboard</h1>
            {profile && (
              <p className="text-sm text-gray-400 mt-1">
                {profile.name || profile.email}
                {profile.verified && (
                  <span className="ml-2 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-green-900/40 text-green-400 border border-green-800">
                    Verified
                  </span>
                )}
              </p>
            )}
          </div>
          <button
            onClick={() => setShowSubmitHint(!showSubmitHint)}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-white text-black text-sm font-medium hover:bg-gray-200 transition-colors"
          >
            <PlusIcon />
            Submit New App
          </button>
        </div>

        {/* Submit hint */}
        {showSubmitHint && (
          <div className="mb-6 p-4 rounded-xl border border-white/10 bg-white/[0.03]">
            <p className="text-sm text-gray-300 mb-2">
              To submit a new app, prepare an app manifest with the following fields:
            </p>
            <ul className="text-xs text-gray-400 list-disc list-inside space-y-1">
              <li>name, slug, description, short_description</li>
              <li>category, icon, screenshots</li>
              <li>pricing (free / paid / freemium), price</li>
              <li>permissions, source_url, version</li>
            </ul>
            <p className="text-xs text-gray-500 mt-2">
              Use the developer API at <code className="text-gray-400">/api/developers/apps/submit</code> to submit programmatically.
            </p>
          </div>
        )}

        {/* Error banner */}
        {error && (
          <div className="mb-6 p-3 rounded-lg bg-red-900/30 border border-red-800 text-red-400 text-sm">
            {error}
          </div>
        )}

        {/* Revenue overview cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-8">
          <RevenueCard
            title="Total Earnings"
            amount={earnings?.total_earnings ?? 0}
            icon={<DollarIcon />}
            subtitle={
              earnings?.monthly && earnings.monthly.length > 0
                ? `${earnings.monthly[earnings.monthly.length - 1].month}: $${earnings.monthly[earnings.monthly.length - 1].amount.toFixed(2)}`
                : undefined
            }
          />
          <RevenueCard
            title="Pending Payout"
            amount={earnings?.pending_payout ?? 0}
            icon={<ClockIcon />}
            subtitle={
              earnings?.last_payout_date
                ? `Last payout: ${new Date(earnings.last_payout_date).toLocaleDateString()}`
                : 'No payouts yet'
            }
          />
          <RevenueCard
            title="Last Payout"
            amount={earnings?.last_payout ?? 0}
            icon={<DollarIcon />}
          />
        </div>

        {/* My apps table */}
        <section>
          <h2 className="text-lg font-semibold mb-4">My Apps</h2>

          {isLoading && apps.length === 0 ? (
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
          ) : apps.length === 0 ? (
            <div className="text-center py-16 text-gray-500 border border-white/5 rounded-xl">
              <p className="text-lg mb-1">No apps submitted yet</p>
              <p className="text-sm">Click &quot;Submit New App&quot; to get started</p>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-white/10">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-left">
                    <th className="px-4 py-3 font-medium text-gray-400">App</th>
                    <th className="px-4 py-3 font-medium text-gray-400">Status</th>
                    <th className="px-4 py-3 font-medium text-gray-400 text-right">Downloads</th>
                    <th className="px-4 py-3 font-medium text-gray-400">Rating</th>
                    <th className="px-4 py-3 font-medium text-gray-400 text-right">Revenue</th>
                  </tr>
                </thead>
                <tbody>
                  {apps.map((app) => (
                    <tr
                      key={app.slug}
                      className="border-b border-white/5 hover:bg-white/[0.02] transition-colors"
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 rounded-lg bg-white/10 flex items-center justify-center text-sm shrink-0">
                            {app.icon || app.name.charAt(0)}
                          </div>
                          <span className="font-medium text-white">{app.name}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={app.status} />
                      </td>
                      <td className="px-4 py-3 text-right text-gray-300">
                        {app.downloads.toLocaleString()}
                      </td>
                      <td className="px-4 py-3">
                        <MiniRating rating={app.rating} />
                      </td>
                      <td className="px-4 py-3 text-right text-gray-300">
                        ${app.revenue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
