import { test, expect } from '@playwright/test';

test.describe('Kernel Page', () => {
  test('should respond to /kernel route without server error', async ({ page }) => {
    const response = await page.goto('/kernel');
    expect(response?.status()).toBeLessThan(500);
  });

  test('should redirect /kernel to sign-in without auth', async ({ page }) => {
    await page.goto('/kernel');
    await expect(page).toHaveURL(/sign-in/);
  });

  test('should have security headers on /kernel', async ({ page }) => {
    const response = await page.goto('/kernel');
    const headers = response?.headers();
    expect(headers?.['x-content-type-options']).toBe('nosniff');
    expect(headers?.['x-frame-options']).toBe('DENY');
  });

  test('should have CSP header on /kernel', async ({ page }) => {
    const response = await page.goto('/kernel');
    const csp = response?.headers()?.['content-security-policy'];
    expect(csp).toBeDefined();
    expect(csp).toContain("script-src 'self'");
  });
});

test.describe('Health Page', () => {
  test('should respond to /health route without server error', async ({ page }) => {
    const response = await page.goto('/health');
    expect(response?.status()).toBeLessThan(500);
  });

  test('should have security headers on /health', async ({ page }) => {
    const response = await page.goto('/health');
    const headers = response?.headers();
    expect(headers?.['x-content-type-options']).toBe('nosniff');
    expect(headers?.['x-frame-options']).toBe('DENY');
  });
});
