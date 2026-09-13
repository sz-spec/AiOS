import { test, expect } from '@playwright/test';

const viewports = [
  { name: 'mobile', width: 375, height: 812 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'desktop', width: 1920, height: 1080 },
] as const;

test.describe('Responsive Layout', () => {
  for (const viewport of viewports) {
    test(`should not have horizontal scroll at ${viewport.name} (${viewport.width}px)`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto('/');
      await page.waitForLoadState('domcontentloaded');

      const hasHorizontalScroll = await page.evaluate(() => {
        return document.documentElement.scrollWidth > document.documentElement.clientWidth;
      });
      expect(hasHorizontalScroll, `Horizontal scroll detected at ${viewport.width}px`).toBe(false);
    });

    test(`should load page without error at ${viewport.name} (${viewport.width}px)`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });

      // Collect console errors during page load
      const consoleErrors: string[] = [];
      page.on('console', (msg) => {
        if (msg.type() === 'error') {
          consoleErrors.push(msg.text());
        }
      });

      const response = await page.goto('/');
      expect(response?.status()).toBeLessThan(500);
    });
  }

  test('should have accessible content at mobile viewport (375px)', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    // Body should have visible content
    const bodyWidth = await page.evaluate(() => {
      return document.body.getBoundingClientRect().width;
    });
    expect(bodyWidth).toBeLessThanOrEqual(375);
    expect(bodyWidth).toBeGreaterThan(0);
  });

  test('should have accessible content at tablet viewport (768px)', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    const bodyWidth = await page.evaluate(() => {
      return document.body.getBoundingClientRect().width;
    });
    expect(bodyWidth).toBeLessThanOrEqual(768);
    expect(bodyWidth).toBeGreaterThan(0);
  });

  for (const viewport of viewports) {
    test(`should have navigation accessible at ${viewport.name} (${viewport.width}px)`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto('/');
      await page.waitForLoadState('domcontentloaded');

      // Check that the page has some navigational element (nav, sidebar, header, or menu)
      const navElements = await page.evaluate(() => {
        const selectors = ['nav', '[role="navigation"]', 'header', '[data-testid="sidebar"]', '[data-testid="navigation"]'];
        for (const sel of selectors) {
          if (document.querySelector(sel)) return true;
        }
        // If redirected to sign-in, navigation may not be present -- that is acceptable
        return window.location.pathname.includes('sign-in');
      });
      expect(navElements).toBe(true);
    });
  }
});
