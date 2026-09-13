import { defineConfig } from 'vitest/config';

// Real Convex backend modules: no React/API or Clerk unit mocks.
export default defineConfig({
  test: { name: 'convex', environment: 'node', include: ['test/convex-*.test.ts'] },
});
