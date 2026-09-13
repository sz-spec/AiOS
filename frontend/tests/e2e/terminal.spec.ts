import { test, expect } from '@playwright/test';

test.describe('Terminal Page', () => {
  test('should respond to /terminal route without server error', async ({ page }) => {
    const response = await page.goto('/terminal');
    expect(response?.status()).toBeLessThan(500);
  });

  test('should redirect /terminal to sign-in without auth', async ({ page }) => {
    await page.goto('/terminal');
    await expect(page).toHaveURL(/sign-in/);
  });

  test('should have security headers on /terminal', async ({ page }) => {
    const response = await page.goto('/terminal');
    const headers = response?.headers();
    expect(headers?.['x-content-type-options']).toBe('nosniff');
    expect(headers?.['x-frame-options']).toBe('DENY');
  });

  test('should have CSP header on /terminal', async ({ page }) => {
    const response = await page.goto('/terminal');
    const csp = response?.headers()?.['content-security-policy'];
    expect(csp).toBeDefined();
    expect(csp).toContain("script-src 'self'");
  });

  test('should have proper page title', async ({ page }) => {
    await page.goto('/terminal');
    await page.waitForLoadState('domcontentloaded');
    await expect(page).toHaveTitle(/VOS3/);
  });

  test('should not expose terminal credentials in page source', async ({ page }) => {
    const response = await page.goto('/terminal');
    const body = await response?.text();

    // Ensure no SSH keys, passwords, or connection strings in HTML
    expect(body).not.toMatch(/-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----/);
    expect(body).not.toMatch(/password\s*[:=]\s*["'][^"']+["']/i);
  });
});
