import { expect, it } from 'vitest';
import { copy } from '../../../src/copy/catalogue.ts';

it.each(Object.entries(copy.actions.blocked))(
  'Runs shares the action-block reason entry for %s with Dashboard and shopping',
  (code, entry) => {
    expect(Reflect.get(copy.runs.reasons, code)).toBe(entry);
  },
);
