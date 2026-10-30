import { entry } from '../entry.ts';

/** Catalogue entries of the Dashboard's topBar part (spec section 6.2). Owned by its region. */
export const topBar = {
  accountDetails: entry('Account details'),
  emulator: entry('Emulator'),
  emulatorUnknown: entry('Emulator name unknown'),
  instance: entry('Configured instance:'),
  details: entry('Details'),
  navigation: entry('Navigation'),
  navigationScroll: entry('Scroll to see all pages'),
  groups: [
    { id: 'operate', label: entry('Operate') },
    { id: 'configure', label: entry('Configure') },
    { id: 'system', label: entry('System') },
  ],
  reconnect: entry('Reconnect'),
  chooseEmulator: entry('Choose emulator'),
  reconnectReasons: {
    process_listed: entry('Unavailable while automation runs'),
    sending: entry('Reconnect is sending; wait for its reply'),
    process_list_unavailable: entry('Reconnect needs a readable process list'),
  },
  connection: {
    connected: entry('Connected'),
    connection_unconfirmed: entry('connection not confirmed'),
    adb_offline: entry('adb: emulator offline'),
    adb_unauthorized: entry('adb: not authorized'),
    adb_missing: entry('Not found by adb'),
    adb_unreachable: entry('Cannot connect to the adb server'),
    emulator_unconfigured: entry('No emulator chosen'),
  },
  notRead: entry('No successful status read yet'),
  /** The app's pages (Runs brief RUN-12). */
  appPages: [
    { id: 'dashboard', group: 'operate', label: entry('Dashboard') },
    { id: 'runs', group: 'operate', label: entry('Runs') },
    { id: 'schedule', group: 'operate', label: entry('Schedule') },
    { id: 'history', group: 'operate', label: entry('Run history') },
    { id: 'equipment', group: 'configure', label: entry('Equipment') },
    { id: 'abilities', group: 'configure', label: entry('Abilities') },
    { id: 'collection', group: 'configure', label: entry('Collection') },
    { id: 'profiles', group: 'configure', label: entry('Profiles') },
    { id: 'emulators', group: 'system', label: entry('Emulators') },
    { id: 'recognition', group: 'system', label: entry('Recognition') },
    { id: 'runtime', group: 'system', label: entry('Runtime') },
  ],
} as const;
