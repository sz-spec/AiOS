import { test, expect } from '@playwright/test';

test.describe('Authentication', () => {
  test('should redirect unauthenticated users to sign-in', async ({ page }) => {
    await page.goto('/chat');
    await expect(page).toHaveURL(/sign-in/);
  });

  test('should show sign-in page', async ({ page }) => {
    await page.goto('/sign-in');
    await expect(page).toHaveTitle(/VOS3/);
  });

  test('should protect settings route', async ({ page }) => {
    await page.goto('/settings');
    await expect(page).toHaveURL(/sign-in/);
  });

  test('should allow access to public home page', async ({ page }) => {
    const response = await page.goto('/');
    expect(response?.status()).toBeLessThan(400);
  });
});
