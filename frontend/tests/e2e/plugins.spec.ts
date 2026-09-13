import { test, expect } from '@playwright/test';

test.describe('Plugins Page', () => {
  test('should respond to /plugins route without server error', async ({ page }) => {
    const response = await page.goto('/plugins');
    expect(response?.status()).toBeLessThan(500);
  });

  test('should redirect /plugins to sign-in without auth', async ({ page }) => {
    await page.goto('/plugins');
    await expect(page).toHaveURL(/sign-in/);
  });

  test('should have security headers on /plugins', async ({ page }) => {
    const response = await page.goto('/plugins');
    const headers = response?.headers();
    expect(headers?.['x-content-type-options']).toBe('nosniff');
    expect(headers?.['x-frame-options']).toBe('DENY');
  });
});

test.describe('Marketplace Page', () => {
  test('should respond to /marketplace route without server error', async ({ page }) => {
    const response = await page.goto('/marketplace');
    expect(response?.status()).toBeLessThan(500);
  });

  test('should have security headers on /marketplace', async ({ page }) => {
    const response = await page.goto('/marketplace');
    const headers = response?.headers();
    expect(headers?.['x-content-type-options']).toBe('nosniff');
    expect(headers?.['x-frame-options']).toBe('DENY');
  });
});

test.describe('Tools Page', () => {
  test('should respond to /tools route without server error', async ({ page }) => {
    const response = await page.goto('/tools');
    expect(response?.status()).toBeLessThan(500);
  });

  test('should have security headers on /tools', async ({ page }) => {
    const response = await page.goto('/tools');
    const headers = response?.headers();
    expect(headers?.['x-content-type-options']).toBe('nosniff');
    expect(headers?.['x-frame-options']).toBe('DENY');
  });
});
