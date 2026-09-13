import { test, expect } from '@playwright/test';

test.describe('Agents Page', () => {
  test('should require authentication', async ({ page }) => {
    await page.goto('/agents');
    await expect(page).toHaveURL(/sign-in/);
  });

  test('should respond with valid status', async ({ page }) => {
    const response = await page.goto('/agents');
    expect(response?.status()).toBeLessThan(500);
  });
});
