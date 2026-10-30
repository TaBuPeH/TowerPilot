// @ts-check
/** Equipment state and answers replayed from real routes, including generated supplemental reads. */
import { fileURLToPath } from 'node:url';
import path from 'node:path';
export const EQUIPMENT_FIXTURE_PATH = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../fixtures/equipment-samples.json',
);

/** @typedef {{ config: string, profile: string, saves: string[], index: number, readback?: string, mode: string, delay: number }} EquipmentSession */
/** @param {URL} url @returns {EquipmentSession} */
export function equipmentSession(url) {
  const scenario = url.searchParams.get('view');
  const prefix = scenario === null ? null : `equipment_samples.${scenario}`;
  return {
    config:
      url.searchParams.get('config') ??
      (prefix === null ? 'loadout_patch.read_before_the_first_save' : `${prefix}_config`),
    profile:
      url.searchParams.get('profile') ??
      (prefix === null ? 'ability_policies.read_before_the_first_save' : `${prefix}_profile`),
    saves: (
      url.searchParams.get('saves') ??
      (prefix === null
        ? 'loadout_patch.global_preset_selected'
        : `${prefix}_global_saved,${prefix}_categories_saved,${prefix}_cards_saved,${prefix}_workshop_bots_saved`)
    ).split(','),
    index: 0,
    mode: url.searchParams.get('mode') ?? 'answer',
    delay: Number(url.searchParams.get('delay') ?? '0'),
    ...(url.searchParams.has('readback')
      ? { readback: url.searchParams.get('readback') ?? '' }
      : {}),
  };
}
/** @param {URL} url @param {EquipmentSession | undefined} session */
export function equipmentRead(url, session) {
  if (session === undefined) return undefined;
  if (url.pathname === '/api/config') return session.config;
  if (url.pathname.startsWith('/api/profile-src/')) return session.profile;
  return undefined;
}
/** @param {string} pathname @param {EquipmentSession | undefined} session */
export function equipmentWrite(pathname, session) {
  if (session === undefined || pathname !== '/api/loadout-patch') return undefined;
  const reference = session.saves[Math.min(session.index, session.saves.length - 1)] ?? '';
  session.index += 1;
  if (session.readback !== undefined) session.config = session.readback;
  else if (reference.startsWith('equipment_samples.') && reference.endsWith('_saved'))
    session.config = reference.replace(/_saved$/, '_readback');
  if (reference.startsWith('equipment_samples.') && reference.endsWith('_saved'))
    session.profile = reference.replace(/_saved$/, '_profile');
  return { reference, mode: session.mode, delay: session.delay };
}
