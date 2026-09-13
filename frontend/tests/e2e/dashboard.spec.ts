import { test, expect } from '@playwright/test';

test.describe('Dashboard', () => {
  test('should load home page with valid status', async ({ page }) => {
    const response = await page.goto('/');
    expect(response?.status()).toBeLessThan(400);
  });

  test('should have proper title containing VOS3', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/VOS3/);
  });

  test('should have security headers on home page', async ({ page }) => {
    const response = await page.goto('/');
    const headers = response?.headers();
    expect(headers?.['x-content-type-options']).toBe('nosniff');
    expect(headers?.['x-frame-options']).toBe('DENY');
  });

  test('should not have horizontal scroll at 1920px viewport', async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    const hasHorizontalScroll = await page.evaluate(() => {
      return document.documentElement.scrollWidth > document.documentElement.clientWidth;
    });
    expect(hasHorizontalScroll).toBe(false);
  });

  test('should have expected meta tags', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    // Check viewport meta tag
    const viewport = await page.locator('meta[name="viewport"]').getAttribute('content');
    expect(viewport).toContain('width=device-width');

    // Check charset
    const charset = await page.locator('meta[charset]').count();
    const charsetHttp = await page.locator('meta[http-equiv="content-type"]').count();
    expect(charset + charsetHttp).toBeGreaterThanOrEqual(1);
  });

  test('should have CSP header with nonce on home page', async ({ page }) => {
    const response = await page.goto('/');
    const csp = response?.headers()?.['content-security-policy'];
    expect(csp).toContain("script-src 'self'");
    expect(csp).toContain('nonce-');
  });
});
