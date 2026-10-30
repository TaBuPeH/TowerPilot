// @ts-check
/** Abilities' session replay uses only generated real-route answers. */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
export const ABILITIES_FIXTURE_PATH = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../fixtures/abilities-samples.json',
);
/** @typedef {{ config:string, profile:string, settings:string, vocab:string, saves:string[], index:number, mode:string, delay:number, view:string, readback?:string, settingsReadback?:string }} AbilitiesSession */
/** @param {URL} url @returns {AbilitiesSession} */
export function abilitiesSession(url) {
  const view = url.searchParams.get('view') ?? 'label',
    prefix = `abilities_samples.${view}`;
  return {
    view,
    config: url.searchParams.get('config') ?? `${prefix}_config`,
    profile: url.searchParams.get('profile') ?? `${prefix}_before`,
    settings: url.searchParams.get('settings') ?? `${prefix}_settings`,
    vocab: url.searchParams.get('vocab') ?? `${prefix}_vocab`,
    saves: (url.searchParams.get('saves') ?? `${prefix}_saved`).split(','),
    index: 0,
    mode: url.searchParams.get('mode') ?? 'answer',
    delay: Number(url.searchParams.get('delay') ?? '0'),
    ...(url.searchParams.has('readback')
      ? { readback: url.searchParams.get('readback') ?? '' }
      : {}),
    ...(url.searchParams.has('settingsReadback')
      ? { settingsReadback: url.searchParams.get('settingsReadback') ?? '' }
      : {}),
  };
}
/** @param {URL} url @param {AbilitiesSession|undefined} session */
export function abilitiesRead(url, session) {
  if (session === undefined) return undefined;
  if (url.pathname === '/api/config') return session.config;
  if (url.pathname.startsWith('/api/profile-src/')) return session.profile;
  if (url.pathname === '/api/settings') return session.settings;
  if (url.pathname === '/api/vocab') return session.vocab;
  return undefined;
}
/** @param {string} pathname @param {AbilitiesSession|undefined} session */
export function abilitiesWrite(pathname, session) {
  if (session === undefined || !['/api/settings', '/api/profile-patch'].includes(pathname))
    return undefined;
  const reference = session.saves[Math.min(session.index, session.saves.length - 1)] ?? '';
  session.index += 1;
  session.profile = session.readback ?? `abilities_samples.${session.view}_after`;
  session.settings = session.settingsReadback ?? `abilities_samples.${session.view}_settings_after`;
  return { reference, mode: session.mode, delay: session.delay };
}
