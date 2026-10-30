import path from 'node:path';
import type { Reporter, TestModule, TestSpecification, Vitest } from 'vitest/node';
import { APP } from '../../support/paths.ts';
import { capabilityFailures, proofFailures, WANTED_BASELINE } from './ledger.ts';
import { SAFETY_LEDGER } from './specData.ts';

/** Judge passing proofs from this run; a full run must cover every named runtime file. */
const CAPABILITY_FILE = 'tests/unit/spec/capabilityLedger.test.ts';

export default class LedgerReporter implements Reporter {
  private vitest: Vitest | undefined;
  private fullRun = false;

  onInit(vitest: Vitest): void {
    this.vitest = vitest;
  }

  async onTestRunStart(specifications: readonly TestSpecification[]): Promise<void> {
    if (this.vitest === undefined) throw new Error('Ledger reporter was not initialized');
    const all = await this.vitest.globTestSpecifications();
    const selected = new Set(specifications.map((spec) => spec.moduleId));
    this.fullRun =
      all.length === specifications.length && all.every((spec) => selected.has(spec.moduleId));
  }

  onTestRunEnd(testModules: readonly TestModule[]): void {
    const passed = new Map<string, string[]>();
    for (const module of testModules) {
      const file = path.relative(APP, module.moduleId).split(path.sep).join('/');
      const titles = passed.get(file) ?? [];
      for (const test of module.children.allTests()) {
        if (test.result().state === 'passed') titles.push(test.name);
      }
      passed.set(file, titles);
    }
    const judged = new Set<string>();
    const failures = proofFailures(SAFETY_LEDGER, (file) => {
      if (passed.has(file)) judged.add(file);
      return passed.get(file);
    });
    if (this.fullRun) {
      // Derive this independently of ledger.ts's directory classifier: a stale prefix
      // must not turn a full run into a successful check of zero files.
      const expected = new Set(
        SAFETY_LEDGER.flatMap((row) => row.tests)
          .filter((ref) => ref.ruleId !== null && /\.test\.tsx?$/u.test(ref.file))
          .map((ref) => ref.file),
      );
      const missing = [...expected].filter((file) => !judged.has(file));
      if (expected.size === 0 || judged.size !== expected.size || missing.length > 0) {
        failures.push(
          `judged ${String(judged.size)} of ${String(expected.size)} ledger files; missing: ${missing.join(', ')}`,
        );
      }
      if (!passed.has(CAPABILITY_FILE)) failures.push(`full run is missing ${CAPABILITY_FILE}`);
      console.info(
        `Ledgers: judged ${String(judged.size)} of ${String(expected.size)} named runtime files`,
      );
    }
    if (passed.has(CAPABILITY_FILE)) {
      failures.push(...capabilityFailures(WANTED_BASELINE, [...passed.values()].flat()));
    }
    if (failures.length > 0) {
      console.error(`\nLedgers: ${String(failures.length)} proof(s) missing in this run:`);
      for (const failure of failures) console.error(`  ${failure}`);
      process.exitCode = 1;
    }
  }
}
