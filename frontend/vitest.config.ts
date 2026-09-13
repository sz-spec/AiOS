import { defineConfig } from 'vitest/config';
import path from 'path';

export default defineConfig({
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./test/setup.ts'],
    coverage: {
      provider: 'v8',
      include: ['lib/store/**', 'hooks/**', 'components/**'],
      exclude: ['**/__tests__/**', '**/*.test.ts'],
    },
    deps: {
      inline: ['convex'],
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
      'convex/react': path.resolve(__dirname, 'test/__mocks__/convex-react.ts'),
      '../convex/_generated/api': path.resolve(__dirname, 'test/__mocks__/convex-api.ts'),
    },
  },
});
