import { test, expect } from '@playwright/test';

test.describe('Security Headers', () => {
  const routesToCheck = ['/', '/chat', '/agents', '/builder', '/billing', '/v-core'];

  test('should have CSP header on all responses', async ({ page }) => {
    for (const route of routesToCheck) {
      const response = await page.goto(route);
      const csp = response?.headers()?.['content-security-policy'];
      expect(csp, `CSP header missing on ${route}`).toBeDefined();
    }
  });

  test('should have script-src self with nonce in CSP', async ({ page }) => {
    const response = await page.goto('/');
    const csp = response?.headers()?.['content-security-policy'];
    expect(csp).toContain("script-src 'self'");
    expect(csp).toContain('nonce-');
  });

  test('should not have unsafe-inline in script-src without nonce', async ({ page }) => {
    const response = await page.goto('/');
    const csp = response?.headers()?.['content-security-policy'] ?? '';

    // If unsafe-inline is present, nonce must also be present (nonce overrides unsafe-inline)
    if (csp.includes("'unsafe-inline'")) {
      expect(csp).toContain('nonce-');
    }
  });

  test('should have X-Content-Type-Options nosniff on all routes', async ({ page }) => {
    for (const route of routesToCheck) {
      const response = await page.goto(route);
      const header = response?.headers()?.['x-content-type-options'];
      expect(header, `X-Content-Type-Options missing on ${route}`).toBe('nosniff');
    }
  });

  test('should have X-Frame-Options DENY on all routes', async ({ page }) => {
    for (const route of routesToCheck) {
      const response = await page.goto(route);
      const header = response?.headers()?.['x-frame-options'];
      expect(header, `X-Frame-Options missing on ${route}`).toBe('DENY');
    }
  });

  test('should not have inline scripts without nonce in HTML', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    const inlineScriptsWithoutNonce = await page.evaluate(() => {
      const scripts = document.querySelectorAll('script:not([src])');
      let violating = 0;
      scripts.forEach((script) => {
        if (!script.getAttribute('nonce') && script.textContent?.trim()) {
          violating++;
        }
      });
      return violating;
    });
    expect(inlineScriptsWithoutNonce).toBe(0);
  });
});

test.describe('XSS Prevention', () => {
  test('should not execute XSS payload in URL params', async ({ page }) => {
    // Set up a listener to detect any alert dialogs (XSS execution)
    let alertFired = false;
    page.on('dialog', async (dialog) => {
      alertFired = true;
      await dialog.dismiss();
    });

    await page.goto('/?q=<script>alert(1)</script>');
    await page.waitForLoadState('domcontentloaded');

    // Wait a short time for any async script execution
    await page.waitForTimeout(1000);
    expect(alertFired).toBe(false);
  });
});

test.describe('Auth Redirect Protection', () => {
  test('should redirect all protected routes to sign-in', async ({ page }) => {
    const protectedRoutes = [
      '/agents',
      '/analytics',
      '/billing',
      '/builder',
      '/chat',
      '/create',
      '/developer',
      '/kernel',
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

    for (const route of protectedRoutes) {
      await page.goto(route);
      await expect(page).toHaveURL(/sign-in/, { timeout: 10000 });
    }
  });
});

test.describe('Sensitive Data Leak Prevention', () => {
  test('should not leak API keys or secrets in page source', async ({ page }) => {
    const routes = ['/', '/chat', '/builder', '/billing'];

    for (const route of routes) {
      const response = await page.goto(route);
      const body = await response?.text();

      expect(body, `API key leaked on ${route}`).not.toMatch(/sk_test_[a-zA-Z0-9]{10,}/);
      expect(body, `API key leaked on ${route}`).not.toMatch(/sk_live_[a-zA-Z0-9]{10,}/);
      expect(body, `API key leaked on ${route}`).not.toMatch(/sk-[a-zA-Z0-9]{20,}/); // OpenAI key format
      expect(body, `Secret leaked on ${route}`).not.toMatch(/CLERK_SECRET_KEY/);
      expect(body, `Secret leaked on ${route}`).not.toMatch(/ANTHROPIC_API_KEY/);
      expect(body, `Secret leaked on ${route}`).not.toMatch(/OPENAI_API_KEY/);
      expect(body, `Secret leaked on ${route}`).not.toMatch(/CONVEX_DEPLOY_KEY/);
    }
  });

  test('should not expose sign-out mechanism to unauthenticated users', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    // sign-out link/button should not be visible when not authenticated
    const signOutVisible = await page.locator('[data-testid="sign-out"], a[href*="sign-out"], button:has-text("Sign Out")').count();
    // This checks the page when unauthenticated -- sign-out should not be rendered
    expect(signOutVisible).toBe(0);
  });
});
