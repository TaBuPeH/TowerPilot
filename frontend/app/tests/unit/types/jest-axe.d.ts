// jest-axe ships no types, and @types/jest-axe pulls in @types/jest globals
// that conflict with Vitest. This declares the part the tests use.
declare module 'jest-axe' {
  export interface AxeViolation {
    readonly id: string;
    readonly help: string;
    readonly nodes: readonly unknown[];
  }
  export interface AxeResults {
    readonly violations: readonly AxeViolation[];
  }
  export function axe(
    html: Element | string,
    options?: Record<string, unknown>,
  ): Promise<AxeResults>;
  export const toHaveNoViolations: {
    toHaveNoViolations(results: AxeResults): { pass: boolean; message(): string };
  };
}
