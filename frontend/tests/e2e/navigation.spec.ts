import { test, expect } from '@playwright/test';

test.describe('Navigation', () => {
  const majorRoutes = [
    '/',
    '/agents',
    '/analytics',
    '/billing',
    '/builder',
    '/chat',
    '/create',
    '/developer',
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
  ];

  test('should load all major routes without server errors', async ({ page }) => {
    for (const route of majorRoutes) {
      const response = await page.goto(route);
      expect(response?.status(), `Route ${route} returned server error`).toBeLessThan(500);
    }
  });

  test('should redirect protected routes to sign-in', async ({ page }) => {
    const protectedRoutes = ['/chat', '/agents', '/settings', '/builder', '/v-core', '/billing'];
    for (const route of protectedRoutes) {
      await page.goto(route);
      await expect(page).toHaveURL(/sign-in/, { timeout: 10000 });
    }
  });

  test('should handle browser back navigation', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');
    const initialUrl = page.url();

    // Navigate to another route (will redirect to sign-in for protected routes)
    await page.goto('/chat');
    await page.waitForLoadState('domcontentloaded');

    await page.goBack();
    await page.waitForLoadState('domcontentloaded');

    // After going back, should be at the initial URL or sign-in
    const currentUrl = page.url();
    expect(currentUrl === initialUrl || /sign-in/.test(currentUrl)).toBeTruthy();
  });

  test('should handle browser forward navigation', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    await page.goto('/agents');
    await page.waitForLoadState('domcontentloaded');

    await page.goBack();
    await page.waitForLoadState('domcontentloaded');

    await page.goForward();
    await page.waitForLoadState('domcontentloaded');

    // Should be back at agents or sign-in redirect
    const currentUrl = page.url();
    expect(currentUrl).toMatch(/agents|sign-in/);
  });

  test('should access deep sub-routes without server errors', async ({ page }) => {
    const subRoutes = [
      '/marketplace/test-plugin',
      '/projects/test-project-id',
      '/tools/test-tool-slug',
      '/studio/test-session-id',
    ];
    for (const route of subRoutes) {
      const response = await page.goto(route);
      expect(response?.status(), `Sub-route ${route} returned server error`).toBeLessThan(500);
    }
  });

  test('should maintain correct URL structure after navigation', async ({ page }) => {
    await page.goto('/marketplace');
    await page.waitForLoadState('domcontentloaded');

    const url = new URL(page.url());
    // Should be at /marketplace or redirected to /sign-in
    expect(url.pathname).toMatch(/\/(marketplace|sign-in)/);
  });

  test('should return valid responses for health endpoint', async ({ page }) => {
    const response = await page.goto('/health');
    expect(response?.status()).toBeLessThan(500);
  });

  test('should handle trailing slashes consistently', async ({ page }) => {
    const responseWithSlash = await page.goto('/agents/');
    const responseWithoutSlash = await page.goto('/agents');

    // Both should respond without server errors
    expect(responseWithSlash?.status()).toBeLessThan(500);
    expect(responseWithoutSlash?.status()).toBeLessThan(500);
  });
});
