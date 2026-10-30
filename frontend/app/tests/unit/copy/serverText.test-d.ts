import { describe, expectTypeOf, it } from 'vitest';
import { copy } from '../../../src/copy/catalogue.ts';
import { fromServer, type ServerText } from '../../../src/copy/server.ts';
import type { CopyNode, CopyText } from '../../../src/copy/text.ts';
import { ServerValue, type ServerValueProps } from '../../../src/ui/ServerValue.tsx';

/**
 * Type-level proof for dashboard-spec 6.3 (R5-04, R5-06): server text and
 * catalogue text never stand in for each other, and ServerValue accepts
 * nothing but ServerText. If a line below stopped being an error, its
 * @ts-expect-error would itself fail the check.
 */
function name(): ServerText<'name'> {
  const value = fromServer('name', 'Farm');
  if (value === null) {
    throw new Error('fromServer returned null');
  }
  return value;
}

describe('ServerText', () => {
  it('is a branded string: not CopyText or CopyNode, and no plain string, CopyText or CopyNode is a ServerText', () => {
    expectTypeOf<ServerText>().toExtend<string>();
    expectTypeOf<ServerText>().not.toExtend<CopyText>();
    expectTypeOf<ServerText>().not.toExtend<CopyNode>();
    expectTypeOf<string>().not.toExtend<ServerText>();
    expectTypeOf<CopyText>().not.toExtend<ServerText>();
    expectTypeOf<CopyNode>().not.toExtend<ServerText>();
    expectTypeOf<ServerText<'name'>>().toExtend<ServerValueProps['value']>();
  });

  it('cannot be written as a literal or an object', () => {
    // @ts-expect-error a plain string is not a ServerText
    const plain: ServerValueProps = { value: 'Farm', kind: 'name' };
    // @ts-expect-error catalogue text is not a ServerText
    const catalogue: ServerValueProps = { value: copy.app.name.short, kind: 'name' };
    // @ts-expect-error an object literal cannot be ServerValue's value
    const object: ServerValueProps = { value: {}, kind: 'name' };
    // @ts-expect-error an object literal lacks the brand
    const forged: ServerText = {};
    expectTypeOf(plain).toEqualTypeOf(catalogue);
    expectTypeOf(object).toEqualTypeOf<ServerValueProps>();
    expectTypeOf(forged).toEqualTypeOf<ServerText>();
  });

  it('names its own kind: a name shown as a message is refused', () => {
    // @ts-expect-error the kind must be the value's own
    const wrong: ServerValueProps<'message'> = { value: name(), kind: 'message' };
    expectTypeOf(wrong).toEqualTypeOf<ServerValueProps<'message'>>();
  });

  it('infers the kind from the value alone in a real call (F1-02)', () => {
    const message = fromServer('message', 'First line\nSecond line');
    if (message !== null) {
      // @ts-expect-error a message cannot be drawn with the one-line name styling
      ServerValue({ value: message, kind: 'name' });
      expectTypeOf(ServerValue({ value: message, kind: 'message' })).not.toBeNever();
    }
  });

  it('is never a label', () => {
    // @ts-expect-error server text cannot be a catalogue label
    const label: ServerValueProps = { value: name(), kind: 'name', label: name() };
    expectTypeOf(label).toEqualTypeOf<ServerValueProps>();
  });
});
