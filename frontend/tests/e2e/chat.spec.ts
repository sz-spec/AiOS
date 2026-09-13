import { test, expect } from '@playwright/test';

test.describe('Chat Interface', () => {
  test('should load chat page structure', async ({ page }) => {
    // Note: Will redirect to sign-in without auth, test structure only
    const response = await page.goto('/');
    expect(response?.status()).toBeLessThan(400);
  });

  test('should have proper page title', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/VOS3/);
  });

  test('should have security headers', async ({ page }) => {
    const response = await page.goto('/');
    const headers = response?.headers();
    expect(headers?.['x-content-type-options']).toBe('nosniff');
    expect(headers?.['x-frame-options']).toBe('DENY');
  });

  test('should have CSP header with nonce', async ({ page }) => {
    const response = await page.goto('/');
    const csp = response?.headers()?.['content-security-policy'];
    expect(csp).toContain("script-src 'self'");
    expect(csp).toContain('nonce-');
  });
});
