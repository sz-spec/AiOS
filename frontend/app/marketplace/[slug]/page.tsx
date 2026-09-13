'use client';

import { useState, useEffect } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useMarketplace } from '@/hooks/useMarketplace';

// ───────────── Icons ─────────────

function ArrowLeftIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="19" y1="12" x2="5" y2="12" />
      <polyline points="12 19 5 12 12 5" />
    </svg>
  );
}

function StarIcon({ filled }: { filled: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill={filled ? '#f59e0b' : 'none'} stroke="#f59e0b" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
    </svg>
  );
}

function ShieldIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}

function ChevronLeftIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="15 18 9 12 15 6" />
    </svg>
  );
}

function ChevronRightIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

// ───────────── Sub-components ─────────────

function StarRating({ rating, size = 'md' }: { rating: number; size?: 'sm' | 'md' }) {
  return (
    <span className="flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((s) => (
        <StarIcon key={s} filled={s <= Math.round(rating)} />
      ))}
      <span className={`ml-1 text-gray-400 ${size === 'sm' ? 'text-xs' : 'text-sm'}`}>
        {rating.toFixed(1)}
      </span>
    </span>
  );
}

function ScreenshotsCarousel({ screenshots }: { screenshots: string[] }) {
  const [current, setCurrent] = useState(0);
  const items = screenshots.length > 0 ? screenshots : [null, null, null]; // placeholders

  return (
    <div className="relative">
      <div className="flex gap-4 overflow-x-auto pb-2 snap-x snap-mandatory scrollbar-hide">
        {items.map((src, i) => (
          <div
            key={i}
            className={`shrink-0 w-72 h-44 rounded-lg border border-white/10 snap-center flex items-center justify-center ${
              src ? '' : 'bg-white/5'
            }`}
          >
            {src ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={src} alt={`Screenshot ${i + 1}`} className="w-full h-full object-cover rounded-lg" />
            ) : (
              <span className="text-gray-600 text-sm">Screenshot {i + 1}</span>
            )}
          </div>
        ))}
      </div>
      {items.length > 1 && (
        <div className="flex justify-center gap-1.5 mt-3">
          <button
            onClick={() => setCurrent((p) => Math.max(0, p - 1))}
            className="p-1 rounded-full bg-white/5 hover:bg-white/10 transition-colors text-gray-400"
          >
            <ChevronLeftIcon />
          </button>
          {items.map((_, i) => (
            <span
              key={i}
              className={`w-1.5 h-1.5 rounded-full mt-2 ${i === current ? 'bg-white' : 'bg-white/20'}`}
            />
          ))}
          <button
            onClick={() => setCurrent((p) => Math.min(items.length - 1, p + 1))}
            className="p-1 rounded-full bg-white/5 hover:bg-white/10 transition-colors text-gray-400"
          >
            <ChevronRightIcon />
          </button>
        </div>
      )}
    </div>
  );
}

// ───────────── Page ─────────────

export default function AppDetailPage() {
  const params = useParams();
  const router = useRouter();
  const slug = params.slug as string;

  const { currentApp, reviews, isLoading, error, getApp, install } = useMarketplace();
  const [installing, setInstalling] = useState(false);
  const [installed, setInstalled] = useState(false);

  useEffect(() => {
    if (slug) getApp(slug);
  }, [slug]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleInstall = async () => {
    setInstalling(true);
    const result = await install(slug);
    if (result) setInstalled(true);
    setInstalling(false);
  };

  if (isLoading && !currentApp) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] text-white flex items-center justify-center">
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
    );
  }

  if (error && !currentApp) {
    return (
      <div className="min-h-screen bg-[#0a0a0a] text-white flex flex-col items-center justify-center gap-4">
        <p className="text-red-400">{error}</p>
        <button
          onClick={() => router.push('/marketplace')}
          className="text-sm text-gray-400 hover:text-white transition-colors"
        >
          Back to Marketplace
        </button>
      </div>
    );
  }

  if (!currentApp) return null;

  const app = currentApp;

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Back button */}
        <button
          onClick={() => router.push('/marketplace')}
          className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-white transition-colors mb-6"
        >
          <ArrowLeftIcon />
          Back to Marketplace
        </button>

        {/* App header */}
        <div className="flex flex-col sm:flex-row gap-6 mb-8">
          {/* Icon */}
          <div className="w-20 h-20 rounded-2xl bg-white/10 flex items-center justify-center text-4xl shrink-0">
            {app.icon || app.name.charAt(0)}
          </div>

          {/* Info */}
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-bold tracking-tight">{app.name}</h1>
            <p className="text-sm text-gray-400 mt-1">by {app.developer}</p>

            <div className="flex items-center gap-4 mt-3 flex-wrap">
              <StarRating rating={app.rating} />
              {app.ratings_count > 0 && (
                <span className="text-xs text-gray-500">
                  ({app.ratings_count.toLocaleString()} ratings)
                </span>
              )}
              <span className="flex items-center gap-1 text-xs text-gray-500">
                <DownloadIcon />
                {app.downloads.toLocaleString()} downloads
              </span>
              <span className="text-xs text-gray-500">v{app.version}</span>
            </div>
          </div>

          {/* Install button */}
          <div className="shrink-0 self-start">
            <button
              onClick={handleInstall}
              disabled={installing || installed || app.installed}
              className={`px-6 py-2.5 rounded-xl font-medium text-sm transition-all ${
                installed || app.installed
                  ? 'bg-green-900/30 text-green-400 border border-green-800 cursor-default'
                  : installing
                    ? 'bg-white/10 text-gray-400 cursor-wait'
                    : 'bg-white text-black hover:bg-gray-200'
              }`}
            >
              {installed || app.installed
                ? 'Installed'
                : installing
                  ? 'Installing...'
                  : app.pricing === 'free'
                    ? 'Install'
                    : app.price
                      ? `Install - $${app.price.toFixed(2)}`
                      : 'Install'}
            </button>
          </div>
        </div>

        {/* Screenshots carousel */}
        <section className="mb-10">
          <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-4">
            Screenshots
          </h2>
          <ScreenshotsCarousel screenshots={app.screenshots ?? []} />
        </section>

        {/* Two-column layout: Description + Sidebar */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          {/* Main content */}
          <div className="lg:col-span-2 space-y-8">
            {/* Description */}
            <section>
              <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
                About
              </h2>
              <p className="text-sm text-gray-300 leading-relaxed whitespace-pre-wrap">
                {app.description}
              </p>
            </section>

            {/* Reviews */}
            <section>
              <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-4">
                Reviews ({reviews.length})
              </h2>
              {reviews.length === 0 ? (
                <p className="text-sm text-gray-500">No reviews yet. Be the first to leave a review.</p>
              ) : (
                <div className="space-y-4">
                  {reviews.map((review) => (
                    <div
                      key={review.id}
                      className="p-4 rounded-lg border border-white/10 bg-white/[0.02]"
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-white">{review.user}</span>
                          <StarRating rating={review.rating} size="sm" />
                        </div>
                        <span className="text-xs text-gray-500">
                          {new Date(review.created_at).toLocaleDateString()}
                        </span>
                      </div>
                      <p className="text-sm text-gray-400">{review.comment}</p>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>

          {/* Sidebar */}
          <div className="space-y-6">
            {/* Permissions */}
            <section className="p-4 rounded-xl border border-white/10 bg-white/[0.02]">
              <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3 flex items-center gap-2">
                <ShieldIcon />
                Permissions
              </h3>
              {app.permissions && app.permissions.length > 0 ? (
                <ul className="space-y-2">
                  {app.permissions.map((perm) => (
                    <li key={perm} className="flex items-start gap-2 text-sm text-gray-400">
                      <span className="w-1 h-1 rounded-full bg-gray-500 mt-2 shrink-0" />
                      {perm}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-gray-500">No special permissions required.</p>
              )}
            </section>

            {/* Details card */}
            <section className="p-4 rounded-xl border border-white/10 bg-white/[0.02]">
              <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
                Details
              </h3>
              <dl className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <dt className="text-gray-500">Category</dt>
                  <dd className="text-gray-300">{app.category}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-gray-500">Version</dt>
                  <dd className="text-gray-300">{app.version}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-gray-500">Pricing</dt>
                  <dd className="text-gray-300 capitalize">{app.pricing}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-gray-500">Updated</dt>
                  <dd className="text-gray-300">
                    {app.updated_at ? new Date(app.updated_at).toLocaleDateString() : 'N/A'}
                  </dd>
                </div>
              </dl>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
