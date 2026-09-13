import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server'
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'
import crypto from 'crypto'

// Routes that don't require authentication
const isPublicRoute = createRouteMatcher([
  '/',
  '/sign-in(.*)',
  '/sign-up(.*)',
  '/api/webhooks(.*)',
  '/api/health',
])

// HTTP methods that mutate state and require CSRF validation
const CSRF_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

// Cookie name for the CSRF double-submit token
const CSRF_COOKIE = '__vos3_csrf'

// Header the client must send to prove it read the cookie
const CSRF_HEADER = 'x-csrf-token'

export default clerkMiddleware(async (auth, request: NextRequest) => {
  // Generate CSP nonce for this request
  const nonce = Buffer.from(crypto.randomUUID()).toString('base64')

  // --- CSRF double-submit cookie validation ---
  if (CSRF_METHODS.has(request.method)) {
    const cookieToken = request.cookies.get(CSRF_COOKIE)?.value
    const headerToken = request.headers.get(CSRF_HEADER)

    // API routes from the frontend must include a matching CSRF header.
    // Skip validation only for webhook callbacks (signed externally).
    const path = request.nextUrl.pathname
    const isWebhook = path.startsWith('/api/webhooks') || path.startsWith('/api/billing/webhook')

    if (!isWebhook && !isPublicRoute(request)) {
      if (!cookieToken || !headerToken || headerToken !== cookieToken) {
        return NextResponse.json(
          { error: { code: 'CSRF_VIOLATION', message: 'Missing or invalid CSRF token' } },
          { status: 403 },
        )
      }
    }
  }

  // Build Content-Security-Policy with nonce
  const cspHeader = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}'`,
    `style-src 'self' 'nonce-${nonce}' https://fonts.googleapis.com`,
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: https:",
    "connect-src 'self' https://*.convex.cloud wss://*.convex.cloud https://*.clerk.accounts.dev",
    "frame-ancestors 'none'",
  ].join('; ')

  // Inject VOS API secret only on mutating /api/* requests (POST/PUT/PATCH/DELETE)
  const requestHeaders = new Headers(request.headers)
  const apiSecret = process.env.VOS_API_SECRET
  if (apiSecret && request.nextUrl.pathname.startsWith('/api/') && CSRF_METHODS.has(request.method)) {
    requestHeaders.set('x-api-key', apiSecret)
  }

  // Pass nonce to downstream server components via x-nonce header
  requestHeaders.set('x-nonce', nonce)

  // Protect all non-public routes — redirect unauthenticated users to sign-in.
  // Clerk v7+ exposes `auth.protect()` as a method on the helper (not on
  // the awaited session). Calling it directly throws a redirect response
  // when unauthenticated.
  if (!isPublicRoute(request)) {
    await auth.protect()
  }

  const response = NextResponse.next({ request: { headers: requestHeaders } })

  // Set CSP and nonce headers on the outgoing response
  response.headers.set('Content-Security-Policy', cspHeader)
  response.headers.set('x-nonce', nonce)

  // --- Set or refresh CSRF cookie (double-submit pattern) ---
  const existingCsrf = request.cookies.get(CSRF_COOKIE)?.value
  if (!existingCsrf) {
    const csrfToken = crypto.randomUUID()
    response.cookies.set(CSRF_COOKIE, csrfToken, {
      httpOnly: false,       // JS must be able to read this to send in header
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'strict',
      path: '/',
      maxAge: 60 * 60 * 24,  // 24 hours
    })
  }

  return response
})

export const config = {
  matcher: [
    // Skip Next.js internals and static files
    '/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)',
    // Always run for API routes
    '/(api|trpc)(.*)',
  ],
}
