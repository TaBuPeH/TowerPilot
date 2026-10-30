import { entry, made } from './entry.ts';
import { serverDateText } from './format.ts';

function age(seconds: number): string {
  return seconds < 60
    ? `${String(seconds)} s ago`
    : seconds < 3600
      ? `${String(Math.floor(seconds / 60))} min ago`
      : `${String(Math.floor(seconds / 3600))} h ago`;
}

/**
 * The Emulators page (backend/logs/reviews/ui-pages/emulators-brief.md, M1 to M8). The page
 * keeps four facts apart: what is configured, what the last scan saw, the transport's live
 * state, and what a request did. None of them is a recognized game screen.
 */
export const emulators = {
  pageName: entry('Emulators'),
  target: {
    title: entry('This emulator'),
    connection: entry('Identity and connection'),
    instance: entry('Instance:'),
    account: entry('Account:'),
    noAccount: entry('none chosen'),
    address: entry('Configured address:'),
    notSet: entry('not set yet'),
    port: {
      open: entry('the last scan reached its port'),
      closed: entry('the last scan could not reach its port'),
      unknown: entry('port not probed'),
    },
    live: entry('Live connection:'),
    liveAge: (seconds: number) => made(`read ${age(seconds)}`),
    liveNote: entry(
      'From the status read, not from this page',
      'A configured address, an open port or a running emulator is not a connected device; only the adb server listing the address as a device is. Opening this page never starts an emulator, reconnects or starts setup.',
    ),
    reconnect: entry('Reconnect'),
    boot: entry(
      'The last status read lists a boot runner',
      'It is what that read listed, not a running process seen now, and it is not tied to any click on this page; the Dashboard shows its progress.',
    ),
    unconfigured: entry(
      'No address is configured for this instance yet',
      'A first install: Use this one fills the empty address (the server refuses if one was set meanwhile), Start it and the tools install need none. Nothing connects until you choose.',
    ),
    dashboard: entry('Open Dashboard'),
    dashboardActions: entry('Stop automation or pause harvesting on the Dashboard.'),
  },
  resolution: {
    title: entry('Screen size'),
    dimensions: entry('Dimensions'),
    orientation: entry('Orientation'),
    layout: entry('Layout'),
    result: entry('Result'),
    response: entry('Response'),
    noteLabel: entry('Note'),
    error: entry('Error'),
    checked: entry('Last check'),
    saved: entry('Saved result'),
    check: entry('Check screen size'),
    checking: entry('Checking the screen size'),
    noAddress: entry('Set an address first'),
    size: (width: number, height: number) => made(`${String(width)} x ${String(height)}`),
    portrait: entry('Portrait, as the game needs it'),
    landscape: entry('Not portrait: open The Tower in portrait, then check again'),
    reference: entry('Exactly the reference layout (1080 x 2560)'),
    notReference: entry('Portrait, but not the 1080 x 2560 reference size'),
    failed: entry("The screen size couldn't be read"),
    noAnswer: entry('The screen size check got no answer'),
    unreadable: entry("The screen size answer couldn't be read"),
    status: (status: number) => made(`HTTP ${String(status)}`),
    checkedAgo: (seconds: number) => made(`checked ${age(seconds)}`),
    savedAt: (seconds: number) => made(`saved by an earlier check, ${serverDateText(seconds)}`),
    note: entry(
      'A size is not recognition',
      'The size says how the screen is laid out. Recognition checks that the game screen itself is recognized; a reconnect or a size check never does.',
    ),
  },
  detected: {
    title: entry('Detected on this computer'),
    scanAge: (seconds: number) => made(`scanned ${age(seconds)}`),
    rescan: entry('Scan again'),
    reading: entry('No scan read yet'),
    unavailable: entry("The scan couldn't be read"),
    error: entry('The scan reported an error:'),
    otherInstance: entry('The last scan was for another instance; scan again before choosing'),
    none: entry('Nothing detected: is your emulator installed and running?'),
    columns: {
      emulator: entry('Emulator'),
      instance: entry('Instance'),
      state: entry('State'),
      address: entry('Address'),
      actions: entry('Actions'),
    },
    running: entry('running'),
    off: entry('off'),
    stateUnknown: entry('listed by adb'),
    stateNotReported: entry('state not reported'),
    blueStacksAdb: {
      on: entry('BlueStacks ADB: on for every instance'),
      off: entry('BlueStacks ADB: off for every instance'),
      unknown: entry('BlueStacks ADB: shared setting not confirmed'),
    },
    display: entry('display not ready:'),
    inUse: entry('In use'),
    use: entry('Use this one'),
    start: entry('Start it'),
    openGame: entry('Open The Tower'),
    prepare: entry('Prepare'),
    suggested: entry('Suggested', 'Exactly one emulator is up, so it is the likely one to use.'),
    alsoSeen: entry('also listed as'),
    notProbed: entry('Not probed:'),
    configured: entry('Configured instances'),
    portOpen: entry('reachable'),
    portClosed: entry('not reachable'),
    portUnknown: entry('not probed'),
    confirmUse: {
      title: entry('Use this emulator?'),
      body: (instance: string, account: string, from: string, to: string) =>
        made(
          `Instance ${instance} (account ${account}) will use ${to} instead of ${from}. Unsaved edits stay with the emulator they were made for.`,
        ),
      confirm: entry('Use it'),
      cancel: entry('Cancel'),
    },
    confirmPrepare: {
      title: entry('Prepare BlueStacks?'),
      body: entry(
        "Writes BlueStacks' own bluestacks.conf: Android Debug Bridge on for every BlueStacks instance; the selected instance's display 2560 x 1080 landscape at 360 dpi (the game turns it to portrait), with custom resolution selected. A backup of the file is kept. Every BlueStacks window must be closed first; this page never closes them.",
      ),
      confirm: entry('Write bluestacks.conf'),
      cancel: entry('Cancel'),
    },
  },
  tools: {
    title: entry('Connection tools'),
    identity: entry(
      'Android platform tools',
      'Installed once on this computer. Keep The Tower installed and signed in on your emulator.',
    ),
    installed: entry('Installed'),
    notInstalled: entry('Not installed'),
    reading: entry('Not read yet'),
    unavailable: entry("The tools state couldn't be read"),
    stage: entry('Stage:'),
    percent: (percent: number) => made(`${String(percent)} %`),
    progress: entry('Download progress'),
    path: entry('Installed at:'),
    readAge: (seconds: number) => made(`read ${age(seconds)}`),
    license: entry('I accept the Android SDK terms for this download'),
    install: entry('Install connection tools'),
    check: entry('Read the progress again'),
    leaving: entry(
      'Leaving this page stops following the progress; it does not cancel the install',
    ),
  },
  preview: {
    title: entry('Video preview settings'),
    intro: entry(
      'Saved settings for the preview, not a live video',
      'They apply on the next preview connection; nothing restarts. The live preview is on the Dashboard, separate from the capture automation uses.',
    ),
    dashboard: entry('Live preview: Dashboard'),
    readAge: (seconds: number) => made(`read ${age(seconds)}`),
    check: entry('Read the saved settings again'),
    states: {
      not_read: entry('The saved settings are not read yet'),
      unavailable: entry("The saved settings couldn't be read"),
      other_context: entry(
        'The last settings read was for another emulator, account or profile',
        'Its values are not shown or used here; read them again.',
      ),
      no_module: entry(
        'The saved settings have no video preview part',
        'The settings this server sent do not include the preview settings, so there is nothing to edit here.',
      ),
    },
  },
  reasons: {
    context_unknown: entry('The emulator and account are not confirmed yet'),
    scan_not_read: entry('No scan read yet'),
    scan_other_instance: entry('Scan again: the last scan was for another instance'),
    write_in_flight: entry('Waiting for the reply to the last request'),
    adopt_unresolved: entry(
      'The last address change has no reply yet; waiting for the next configuration read',
    ),
    in_use: entry('Already in use'),
    launch_wait_scan: entry('Start requested; scan again before starting it again'),
    prepare_wait_scan: entry('Prepared; scan again to see the result'),
    bluestacks_running: entry('Close every BlueStacks window first'),
    tools_not_read: entry('The tools state has not been read yet'),
    tools_installed: entry('Already installed'),
    install_running: entry('An install is running'),
    install_wait_read: entry('Waiting for the next progress read'),
    accept_license: entry('Accept the Android SDK terms first'),
    sending: entry('Waiting for the reply'),
    process_list_unavailable: entry('The process list is not read yet'),
    process_listed: entry('Automation is running; stop it on the Dashboard first'),
    choose_emulator: entry('Choose an emulator first'),
    ready: entry('Ready'),
  },
  outcomes: {
    lead: entry('Last request:'),
    answeredAgo: (seconds: number) => made(age(seconds)),
    restored: entry('kept from before the page was reloaded'),
    sending: entry('Sent - waiting for the reply'),
    adopt: {
      saved: entry('Saved as the address of this instance'),
      connected: entry('Its connect attempt: connected'),
      notConnected: entry('Its connect attempt: not connected'),
      connectUnknown: entry('Its connect attempt: not reported'),
    },
    launch: {
      accepted: entry(
        'Start requested',
        'The emulator manager accepted it. This page cannot tell when the game is ready: scan again to see the emulator running, and Recognition shows the game screen.',
      ),
      failed: entry('The emulator manager reported a failure'),
    },
    prepare: {
      done: entry('bluestacks.conf written'),
      nothing: entry('Nothing to change: already prepared'),
      backup: entry('Backup:'),
      added: entry('Added:'),
    },
    install: {
      accepted: entry('Install started; the progress reads show how it goes'),
      already_running: entry('An install was already running; it was not restarted'),
    },
    reconnect: {
      done: entry('Connected: the adb server lists the address as a device'),
      failed: entry('Not connected: the adb server reports'),
    },
    refused: entry(
      'Not done',
      'The server answered that it did not do this request; its reason is below. Nothing is sent again.',
    ),
    unknown: entry(
      'No confirmed reply - it may or may not have been done',
      'Nothing is sent again on its own. The next reads show what is configured and running.',
    ),
    applied: entry('A later read shows it was done'),
    serverError: entry('The server reported:'),
    not_applied: entry('A later read shows it was not done'),
    dismiss: entry('Dismiss'),
  },
  notSent: {
    collection_write_in_flight: entry('Not sent - wait until the pending edit has been read back'),
    emulator_write_in_flight: entry('Not sent - the last request is still waiting for its reply'),
    process_listed: entry('Not sent - automation is running; stop it on the Dashboard first'),
    process_list_unavailable: entry('Not sent - the process list is not read yet'),
    context_changed: entry(
      'Not sent - the emulator or account changed; check the page and choose again',
    ),
    not_launchable: entry('Not sent - this emulator cannot be started from here'),
  },
} as const;
