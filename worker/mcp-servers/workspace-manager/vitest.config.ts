import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    testTimeout: 30000,
    pool: 'forks',
    poolOptions: { forks: { maxForks: 2, minForks: 1 } },
  },
});
