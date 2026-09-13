import { test, expect } from '@playwright/test';

test.describe('Billing Page', () => {
  test('should respond to /billing route without server error', async ({ page }) => {
    const response = await page.goto('/billing');
    expect(response?.status()).toBeLessThan(500);
  });

  test('should redirect /billing to sign-in without auth', async ({ page }) => {
    await page.goto('/billing');
    await expect(page).toHaveURL(/sign-in/);
  });

  test('should have security headers on /billing', async ({ page }) => {
    const response = await page.goto('/billing');
    const headers = response?.headers();
    expect(headers?.['x-content-type-options']).toBe('nosniff');
    expect(headers?.['x-frame-options']).toBe('DENY');
  });

  test('should have CSP header on /billing', async ({ page }) => {
    const response = await page.goto('/billing');
    const csp = response?.headers()?.['content-security-policy'];
    expect(csp).toBeDefined();
    expect(csp).toContain("script-src 'self'");
  });

  test('should not expose sensitive data in initial HTML', async ({ page }) => {
    const response = await page.goto('/billing');
    const body = await response?.text();

    // Ensure no API keys, secrets, or tokens are present in the HTML source
    expect(body).not.toMatch(/sk_test_[a-zA-Z0-9]+/);
    expect(body).not.toMatch(/sk_live_[a-zA-Z0-9]+/);
    expect(body).not.toMatch(/pk_test_[a-zA-Z0-9]+/);
    expect(body).not.toMatch(/pk_live_[a-zA-Z0-9]+/);
    expect(body).not.toMatch(/whsec_[a-zA-Z0-9]+/);
    expect(body).not.toMatch(/OPENAI_API_KEY/);
    expect(body).not.toMatch(/ANTHROPIC_API_KEY/);
    expect(body).not.toMatch(/CLERK_SECRET_KEY/);
  });

  test('should have proper page title', async ({ page }) => {
    await page.goto('/billing');
    await page.waitForLoadState('domcontentloaded');
    await expect(page).toHaveTitle(/VOS3/);
  });
});
