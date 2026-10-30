import { expectTypeOf, it } from 'vitest';
import type { copy } from '../../../../../src/copy/catalogue.ts';
import type { ServerText } from '../../../../../src/copy/server.ts';

type Words = typeof copy.dashboard.needsYou;

it('F-083: setup date formatting accepts only closed outcome codes', () => {
  type Code = Parameters<Words['dated']>[0];
  expectTypeOf<string>().not.toExtend<Code>();
  expectTypeOf<ServerText>().not.toExtend<Code>();
  expectTypeOf<'setup_failed'>().toExtend<Code>();
});

it('F-084: checked-read formatting accepts only source and outcome codes', () => {
  type Source = Parameters<Words['checked']>[0];
  type Outcome = Parameters<Words['checked']>[1];
  expectTypeOf<string>().not.toExtend<Source>();
  expectTypeOf<ServerText>().not.toExtend<Source>();
  expectTypeOf<string>().not.toExtend<Outcome>();
  expectTypeOf<ServerText>().not.toExtend<Outcome>();
  expectTypeOf<'status'>().toExtend<Source>();
  expectTypeOf<'failed'>().toExtend<Outcome>();
});
