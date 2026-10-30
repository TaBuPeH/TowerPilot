import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// Dates render in the local zone; the reviewed output dumps were recorded in this one. Pin it so a runner in
// another zone (CI runs in UTC) renders the same text.
process.env['TZ'] = 'America/Denver';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    // r6 is the production contract (B-04): tests/unit/r6 runs in every run.
    include: ['tests/unit/**/*.test.{ts,tsx}'],
    setupFiles: ['./tests/unit/setup.ts'],
    // The safety ledger is judged from this run's own results (tests/unit/spec/ledgerReporter.ts).
    reporters: ['default', './tests/unit/spec/ledgerReporter.ts'],
    // Whole-app page tests take about 1 s alone and several times that on a loaded runner; the 5 s default timed out (RUN-10).
    testTimeout: 30_000,
    hookTimeout: 30_000,
    restoreMocks: true,
    unstubGlobals: true,
    typecheck: {
      enabled: true,
      tsconfig: './tsconfig.test.json',
      include: ['tests/unit/**/*.test-d.ts'],
    },
  },
});
