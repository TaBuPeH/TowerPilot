import { describe, expect, it } from 'vitest';
import { gemSlot, turnOnView } from '../../../src/core/harvestView.ts';
import { opaqueToken, sameToken, tokenWireValue } from '../../../src/core/tokens.ts';
import { sampleRecord, statusBody } from '../../support/fixture.ts';
import { contextOf, rec, TURN_ON_READY } from '../../support/core.ts';

const ALLOW = { blocks_launch: false, scope: 'collection', code: null, message: null };

describe('S-016 opaque tokens are compared for equality only', () => {
  it.each(['overlay_applied', 'source_changed'])(
    'S-016 %s: request_key and policy_revision of another format change nothing',
    (name) => {
      const original = sampleRecord(`status.${name}`);
      const changed = sampleRecord(`status.${name}`);
      if (rec(changed)['overlay'] !== null) {
        rec(rec(changed)['overlay'])['request_key'] = '{"not": "a key"}';
      }
      rec(rec(changed)['source'])['policy_revision'] = '-1';
      rec(rec(changed)['desired'])['request_key'] = '99999999999999999999';
      rec(rec(rec(changed)['desired'])['bound'])['policy_revision'] = 'zzz';
      const before = gemSlot(contextOf(statusBody(original)), null, []);
      const after = gemSlot(contextOf(statusBody(changed)), null, []);
      expect(after).toEqual(before);
      expect(turnOnView(contextOf(statusBody(changed)), ALLOW, TURN_ON_READY).offered).toBe(
        turnOnView(contextOf(statusBody(original)), ALLOW, TURN_ON_READY).offered,
      );
    },
  );

  it('S-016 tokens compare by equality only, whatever their format; no order, no parse', () => {
    expect(sameToken(opaqueToken('0001'), opaqueToken('0001'))).toBe(true);
    expect(sameToken(opaqueToken('0001'), opaqueToken('1'))).toBe(false);
    expect(sameToken(opaqueToken('a'), null)).toBe(false);
    expect(opaqueToken('')).toBeNull();
    expect(opaqueToken(1)).toBeNull();
    const token = opaqueToken('req-000001');
    expect(token).not.toBeNull();
    if (token !== null) {
      // nothing renders or serialises its value; only the request body writer reads it
      expect(JSON.stringify(token)).toBe('{}');
      // eslint-disable-next-line @typescript-eslint/no-base-to-string -- deliberate: stringifying a token shows nothing of it
      expect(String(token)).not.toContain('req-000001');
      expect(Object.keys(token)).toEqual([]);
      expect(tokenWireValue(token)).toBe('req-000001');
    }
  });

  it('S-016 control: an unchanged policy_revision keeps "source unchanged"', () => {
    const harvest = sampleRecord('status.off');
    const revision = rec(rec(harvest)['source'])['policy_revision'];
    expect(
      sameToken(
        opaqueToken(revision),
        opaqueToken(rec(rec(rec(harvest)['desired'])['bound'])['policy_revision']),
      ),
    ).toBe(true);
    expect(turnOnView(contextOf(statusBody(harvest)), ALLOW, TURN_ON_READY)).toMatchObject({
      offered: true,
      sourceChanged: false,
    });
  });
});
