// @ts-check
/** Schedule replays only backend-generated route answers; no invented profile or revision. */

/** @typedef {{ profile: string, status: string, config: string, save: string, readback?: string, mode: string, delay: number, race: boolean, committed?: string }} ScheduleSession */

/** @param {URL} url @returns {ScheduleSession} */
export function scheduleSession(url) {
  return {
    profile: url.searchParams.get('profile') ?? 'profile_patch.read_before_the_first_save',
    status: url.searchParams.get('status') ?? 'schedule_plan.observed_under_the_active_profile',
    config: url.searchParams.get('config') ?? 'config_state.read_before_the_save',
    save: url.searchParams.get('save') ?? 'profile_patch.multi_save_first_request_saved',
    ...(url.searchParams.has('readback')
      ? { readback: url.searchParams.get('readback') ?? '' }
      : {}),
    mode: url.searchParams.get('mode') ?? 'answer',
    delay: Number(url.searchParams.get('delay') ?? '0'),
    race: url.searchParams.get('race') === 'true',
  };
}

/** @param {URL} url @param {ScheduleSession | undefined} session */
export function scheduleRead(url, session) {
  if (session === undefined) return undefined;
  if (url.pathname === '/api/config') return session.config;
  if (url.pathname.startsWith('/api/profile-src/')) return session.profile;
  if (url.pathname === '/api/schedule/status') return session.status;
  return undefined;
}

/**
 * A competing page receives the real stale-revision answer. An accepted request
 * changes subsequent reads to the configured real readback; neither path forwards.
 * @param {string} pathname
 * @param {ScheduleSession | undefined} session
 * @returns {{reference: string, mode: string, delay: number} | undefined}
 */
export function scheduleWrite(pathname, session) {
  if (session === undefined || !['/api/profile-patch', '/api/run-controls'].includes(pathname))
    return undefined;
  let reference;
  if (session.race && session.committed !== undefined && session.committed !== pathname) {
    reference =
      pathname === '/api/profile-patch'
        ? 'profile_patch.stale_revision_409'
        : 'run_controls_save.stale_revision_another_client_saved_first';
  } else {
    reference =
      pathname === '/api/profile-patch' ? session.save : 'run_controls_save.saved_switch_off';
    session.committed = pathname;
    if (session.readback !== undefined) session.profile = session.readback;
  }
  return { reference, mode: session.mode, delay: session.delay };
}
