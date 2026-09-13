import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';

// Mock next/navigation
vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
}));

// Polyfill crypto.randomUUID for jsdom
if (typeof crypto.randomUUID === 'undefined') {
  Object.defineProperty(crypto, 'randomUUID', {
    value: (): string => {
      const bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = Array.from(bytes).map((b) => b.toString(16).padStart(2, '0')).join('');
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    },
    configurable: true,
  });
}

// Unit-only identity fixture. Convex authorization and Playwright use separate
// configurations and never load this mock. Stable getToken avoids effect loops.
vi.mock('@clerk/nextjs', () => {
  const getToken = vi.fn(async () => 'unit-test-token');
  return { useAuth: () => ({ getToken, isLoaded: true, isSignedIn: true, userId: 'unit-user' }) };
});
