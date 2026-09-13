/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // Optimize barrel imports to avoid pulling entire modules
  experimental: {
    optimizePackageImports: [
      '@/components/agents',
      '@/components/v-core',
      '@/components/bmad',
      '@/components/memory',
    ],
  },
  // Allow connecting to backend API
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/:path*`,
      },
    ];
  },
  // Security headers applied to all routes
  // NOTE: Content-Security-Policy is set dynamically in middleware.ts with per-request nonce
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          {
            key: 'X-Frame-Options',
            value: 'DENY',
          },
          {
            key: 'X-Content-Type-Options',
            value: 'nosniff',
          },
          {
            key: 'Referrer-Policy',
            value: 'strict-origin-when-cross-origin',
          },
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=31536000; includeSubDomains',
          },
          {
            key: 'Permissions-Policy',
            value: 'camera=(), microphone=(), geolocation=()',
          },
        ],
      },
      // CVE-2026-29057: Block chunked Transfer-Encoding on DELETE/OPTIONS
      // through rewrite routes to mitigate HTTP request smuggling.
      // Full fix requires Next.js 15.5.13+ (major upgrade deferred).
      {
        source: '/api/:path*',
        headers: [
          {
            key: 'Transfer-Encoding',
            value: '',
          },
        ],
        has: [
          {
            type: 'header',
            key: 'Transfer-Encoding',
            value: 'chunked',
          },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
