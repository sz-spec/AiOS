import { test, expect } from '@playwright/test';

test.describe('Builder Page', () => {
  test('should respond to /builder route without server error', async ({ page }) => {
    const response = await page.goto('/builder');
    expect(response?.status()).toBeLessThan(500);
  });

  test('should redirect /builder to sign-in without auth', async ({ page }) => {
    await page.goto('/builder');
    await expect(page).toHaveURL(/sign-in/);
  });

  test('should have proper page title', async ({ page }) => {
    await page.goto('/builder');
    await page.waitForLoadState('domcontentloaded');
    await expect(page).toHaveTitle(/VOS3/);
  });

  test('should have security headers on /builder', async ({ page }) => {
    const response = await page.goto('/builder');
    const headers = response?.headers();
    expect(headers?.['x-content-type-options']).toBe('nosniff');
    expect(headers?.['x-frame-options']).toBe('DENY');
  });

  test('should have CSP header on /builder', async ({ page }) => {
    const response = await page.goto('/builder');
    const csp = response?.headers()?.['content-security-policy'];
    expect(csp).toBeDefined();
    expect(csp).toContain("script-src 'self'");
  });
});

test.describe('Create Page', () => {
  test('should respond to /create route without server error', async ({ page }) => {
    const response = await page.goto('/create');
    expect(response?.status()).toBeLessThan(500);
  });

  test('should redirect /create to sign-in without auth', async ({ page }) => {
    await page.goto('/create');
    await expect(page).toHaveURL(/sign-in/);
  });

  test('should have security headers on /create', async ({ page }) => {
    const response = await page.goto('/create');
    const headers = response?.headers();
    expect(headers?.['x-content-type-options']).toBe('nosniff');
    expect(headers?.['x-frame-options']).toBe('DENY');
  });
});
