import { test, expect } from '@playwright/test';

test.describe('V-Core Page', () => {
  test('should respond to /v-core route without server error', async ({ page }) => {
    const response = await page.goto('/v-core');
    expect(response?.status()).toBeLessThan(500);
  });

  test('should redirect /v-core to sign-in without auth', async ({ page }) => {
    await page.goto('/v-core');
    await expect(page).toHaveURL(/sign-in/);
  });

  test('should have proper page title', async ({ page }) => {
    await page.goto('/v-core');
    await page.waitForLoadState('domcontentloaded');
    await expect(page).toHaveTitle(/VOS3/);
  });

  test('should have security headers on /v-core', async ({ page }) => {
    const response = await page.goto('/v-core');
    const headers = response?.headers();
    expect(headers?.['x-content-type-options']).toBe('nosniff');
    expect(headers?.['x-frame-options']).toBe('DENY');
  });

  test('should have CSP header on /v-core', async ({ page }) => {
    const response = await page.goto('/v-core');
    const csp = response?.headers()?.['content-security-policy'];
    expect(csp).toBeDefined();
    expect(csp).toContain("script-src 'self'");
    expect(csp).toContain('nonce-');
  });

  test('should not have inline scripts without nonce attribute', async ({ page }) => {
    await page.goto('/v-core');
    await page.waitForLoadState('domcontentloaded');

    const inlineScriptsWithoutNonce = await page.evaluate(() => {
      const scripts = document.querySelectorAll('script:not([src])');
      let count = 0;
      scripts.forEach((script) => {
        if (!script.getAttribute('nonce') && script.textContent?.trim()) {
          count++;
        }
      });
      return count;
    });
    expect(inlineScriptsWithoutNonce).toBe(0);
  });
});

test.describe('Workflows Page', () => {
  test('should respond to /workflows route without server error', async ({ page }) => {
    const response = await page.goto('/workflows');
    expect(response?.status()).toBeLessThan(500);
  });

  test('should redirect /workflows to sign-in without auth', async ({ page }) => {
    await page.goto('/workflows');
    await expect(page).toHaveURL(/sign-in/);
  });

  test('should have security headers on /workflows', async ({ page }) => {
    const response = await page.goto('/workflows');
    const headers = response?.headers();
    expect(headers?.['x-content-type-options']).toBe('nosniff');
    expect(headers?.['x-frame-options']).toBe('DENY');
  });

  test('should have CSP header on /workflows', async ({ page }) => {
    const response = await page.goto('/workflows');
    const csp = response?.headers()?.['content-security-policy'];
    expect(csp).toBeDefined();
    expect(csp).toContain("script-src 'self'");
  });
});
