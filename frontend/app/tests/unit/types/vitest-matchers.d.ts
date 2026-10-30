import 'vitest';

declare module 'vitest' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- merging needs Vitest's exact type parameters
  interface Matchers<R extends void | Promise<void> = void | Promise<void>, T = unknown> {
    /** jest-axe: the axe results contain no violations. */
    toHaveNoViolations(): R;
  }
}
