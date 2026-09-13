import { test as base, expect, Page, APIResponse } from '@playwright/test';

/**
 * Auth fixture for VOS3 E2E tests.
 *
 * Since Clerk test mode is not available in this environment, this fixture
 * provides helpers for verifying auth redirect behavior and security headers.
 * When authenticated session testing becomes available, extend this fixture
 * with storageState-based authentication.
 */

/** Routes that require authentication and should redirect to /sign-in */
export const PROTECTED_ROUTES = [
  '/agents',
  '/analytics',
  '/billing',
  '/builder',
  '/chat',
  '/create',
  '/developer',
  '/github',
  '/kernel',
  '/marketplace',
  '/memory',
  '/metrics',
  '/plugins',
  '/projects',
  '/rag',
  '/settings',
  '/studio',
  '/terminal',
  '/tools',
  '/v-core',
  '/workflows',
] as const;

/** Routes that may be publicly accessible */
export const PUBLIC_ROUTES = [
  '/',
  '/health',
] as const;

/**
 * Check that a route redirects to the sign-in page.
 * Clerk middleware intercepts unauthenticated requests and sends them to /sign-in.
 */
export async function expectAuthRedirect(page: Page, route: string): Promise<void> {
  await page.goto(route);
  await expect(page).toHaveURL(/sign-in/, { timeout: 10000 });
}

/**
 * Check that a response has the expected security headers.
 */
export async function expectSecurityHeaders(response: APIResponse | null): Promise<void> {
  expect(response).not.toBeNull();
  const headers = response!.headers();
  expect(headers['x-content-type-options']).toBe('nosniff');
  expect(headers['x-frame-options']).toBe('DENY');
}

/**
 * Check that a response has a valid CSP header with nonce-based script-src.
 */
export async function expectCSPHeader(response: APIResponse | null): Promise<void> {
  expect(response).not.toBeNull();
  const csp = response!.headers()['content-security-policy'];
  expect(csp).toBeDefined();
  expect(csp).toContain("script-src 'self'");
  expect(csp).toContain('nonce-');
}

/**
 * Check that a response has no server error (status < 500).
 */
export async function expectNoServerError(response: APIResponse | null): Promise<void> {
  expect(response).not.toBeNull();
  expect(response!.status()).toBeLessThan(500);
}

/**
 * Extended test fixture. Currently identical to the base Playwright test
 * but provides a hook for future authenticated session support via storageState.
 *
 * Usage:
 *   import { test } from './fixtures/auth.fixture';
 *   test('my test', async ({ page }) => { ... });
 *
 * Future: Add storageState for authenticated sessions:
 *   export const test = base.extend({
 *     storageState: 'tests/e2e/.auth/user.json',
 *   });
 */
export const test = base.extend({});

export { expect };
