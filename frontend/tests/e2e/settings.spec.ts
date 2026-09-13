import { test, expect } from '@playwright/test';

test.describe('Settings Page', () => {
  test('should require authentication', async ({ page }) => {
    await page.goto('/settings');
    await expect(page).toHaveURL(/sign-in/);
  });

  test('should respond with valid status', async ({ page }) => {
    const response = await page.goto('/settings');
    expect(response?.status()).toBeLessThan(500);
  });
});
