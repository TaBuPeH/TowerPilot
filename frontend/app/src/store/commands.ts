import type { ProfileWrite } from '../api/intents/profiles.ts';
import type { ProfilePatchCommand } from './selectors/collection.ts';
import type { EmulatorWriteCommand } from './selectors/emulators.ts';
import type {
  RecognitionApplyCommand,
  RecognitionLaunchCommand,
  RecognitionStopCommand,
} from './selectors/recognition.ts';
import type { SettingsSaveCommand } from './selectors/settingsModule.ts';
import type { ReconnectCommand } from './selectors/topBar.ts';
import type { ContextView } from './types.ts';
import type { AddRunCommand } from './selectors/runsAdd.ts';
import type { RunEntryCommand } from './selectors/runEntry.ts';

/**
 * The page's commands (spec 7.1): declared in store/ and implemented in app/
 * with api/ intents, because store/ may not import api/. Components call these
 * on a person's click; nothing else calls them. Each returns when the click's
 * own work is done (a Pause click may read config and harvest first; a Start
 * reads readiness first). None of them retries anything.
 *
 * Every command that names a target carries the context epoch of the view
 * model it came from (S-014, S-101): app/wiring.ts sends nothing when the
 * page's context changed since, so a target captured in one emulator or
 * account is never sent with another's context.
 */
export interface Fenced {
  /** DashboardState.epoch when the view model offering this command was drawn. */
  readonly epoch: number;
}

/**
 * A Profiles write (profiles brief P1 to P5) as its view model offered it: the
 * write, the page context it was drawn in (the account and profile routes act
 * on whatever the server binds at that moment, so app/ sends it only while a
 * fresh config read still names this context), and the draft it came from.
 */
export interface ProfileWriteCommand extends Fenced {
  readonly context: ContextView;
  readonly action: ProfileWrite;
  readonly draft: {
    readonly scope: string;
    readonly values: Readonly<Record<string, string | boolean>>;
  } | null;
}

export type StopCommand =
  | (Fenced & {
      readonly kind: 'cancel';
      readonly operationId: string;
      readonly expectedRevision: number;
    })
  | (Fenced & { readonly kind: 'kill'; readonly pid: number; readonly created: number });

export interface TurnOnCommand extends Fenced {
  readonly preset: string;
  readonly expectedRevision: number;
}

export interface ClearFlagCommand extends Fenced {
  readonly operationId: string;
  readonly expectedRevision: number;
}

/** What the handoff dialog's confirmation binds (S-101): the target on screen and the attestation. */
export interface HandoffCommand extends Fenced {
  readonly operationId: string;
  readonly setupBattleId: string;
  readonly evidenceRevision: string;
  readonly attestation: 'new_manual_battle';
}

export interface LaunchCommand extends Fenced {
  readonly preset: string;
}

export interface RunNowCommand extends Fenced {
  readonly operationId: string;
  readonly battleId: string;
  readonly generation: string;
  readonly expectedRevision: number;
  readonly action: string;
}

/** RUN-05: a saved-run save as the view model offered it, with the draft text it came from. */
export interface SaveRunCommand extends Fenced {
  readonly scope: string;
  readonly blueprint: string;
  /** The context the run's read named (sent back: the server refuses another). */
  readonly context: unknown;
  readonly expectedRevision: string;
  readonly changes: Readonly<Record<string, unknown>>;
  /** The draft values these changes came from: cleared only when this save is confirmed. */
  readonly draft: Readonly<Record<string, string | boolean>>;
}

/** RUN-08: a live change of the tracked battle, with the draft text it came from. */
export interface SaveLiveCommand extends Fenced {
  readonly scope: string;
  readonly operationId: string;
  readonly battleId: string;
  readonly generation: string;
  readonly expectedRevision: number;
  readonly changes: Readonly<Record<string, unknown>>;
  readonly draft: Readonly<Record<string, string | boolean>>;
  /** The battle's label as the read named it (identifies the line once another battle is shown). */
  readonly battleLabel: string | null;
}

/** F6-11 RL1, RL2: a reset of this battle's overrides, as the live editor offered it. */
export interface ResetLiveCommand extends Fenced {
  readonly operationId: string;
  readonly battleId: string;
  readonly generation: string;
  readonly expectedRevision: number;
  readonly paths: readonly string[];
  readonly battleLabel: string | null;
}

export interface DashboardCommands {
  /** INV-1: one Pause click (context per OD-13; nothing new while one is in flight; the same request again while unknown). */
  readonly pause: () => Promise<void>;
  readonly turnOn: (command: TurnOnCommand) => Promise<void>;
  /** INV-2: always a new request, never queued. */
  readonly turnInputOff: () => Promise<void>;
  readonly allowInput: () => Promise<void>;
  /** INV-3: a Stop (cancel, with the highest revision known at the click) or a kill of the exact row. */
  readonly stop: (command: StopCommand) => Promise<void>;
  readonly clearFlag: (command: ClearFlagCommand) => Promise<void>;
  readonly handoff: (command: HandoffCommand) => Promise<void>;
  readonly start: (command: LaunchCommand) => Promise<void>;
  readonly collect: (command: LaunchCommand) => Promise<void>;
  /**
   * F-004, M8: reconnect the address the view model showed (sent in the body), after a
   * click-time config read still names the same context; one connection request at a time.
   */
  readonly reconnect: (command: ReconnectCommand) => Promise<void>;
  readonly runNow: (command: RunNowCommand) => Promise<void>;
  /** A person's explicit resend of the identical request (only where 7.4 offers one). */
  readonly resend: (intentId: string, confirmedAgain?: boolean) => Promise<void>;
  /** OD-10: hide one outcome line; sends nothing. */
  readonly dismiss: (intentId: string) => void;
  /**
   * The person continues from the newest read while a save's outcome is unknown ("accept the
   * current state"): the record stays unknown and journaled; nothing is sent.
   */
  readonly acceptCurrent: (intentId: string) => void;
  /** OD-9 Check status / Retry status: a forced status read, never a write. */
  readonly checkStatus: () => void;
  /** F-082 Retry for setup progress: a forced calibration read, never a write. */
  readonly checkSetupStatus: () => void;
  readonly selectRun: (run: string | null) => void;
  /** F-026: the "Gem settings from" picker; sends nothing (its saved settings are read once, F-111). */
  readonly selectGemSource: (run: string | null) => void;
  /** Runs page (RUN-01, RUN-12): the page is open on `run` (null: its default); sends nothing. */
  readonly openRuns: (open: boolean, run: string | null) => void;
  /** RUN-03: a person's edit of one field; sends nothing. */
  readonly editDraft: (scope: string, path: string, value: string | boolean) => void;
  /** RUN-03: Discard every edit of the scope; sends nothing. */
  readonly discardDraft: (scope: string) => void;
  readonly saveRun: (command: SaveRunCommand) => Promise<void>;
  readonly addRun: (command: AddRunCommand) => Promise<void>;
  readonly checkRunAdd: () => void;
  readonly saveLive: (command: SaveLiveCommand) => Promise<void>;
  /** F6-11 RL1: send the reset the live editor offered (one POST, never resent on its own). */
  readonly resetLive: (command: ResetLiveCommand) => Promise<void>;
  /** RUN-06, RUN-07: "Check saved values" reads the shown run again (a GET, never a write). */
  readonly checkRun: () => void;
  readonly checkRuns: () => void;
  /** RUN-10: read the tracked battle again (a GET, never a write). */
  readonly checkBattle: () => void;
  /** The Profiles page opened or closed: its profile files and active profile are read while open. */
  readonly pickEditingTarget: (command: Fenced & { readonly name: string }) => void;
  readonly checkEditingProfiles: () => void;
  readonly returnEditingToActive: (command: Fenced) => void;
  readonly openProfiles: (open: boolean) => void;
  /**
   * P5: read one profile's source (a GET) to show its validation before it may be activated;
   * the answer is kept for this context only.
   */
  readonly checkProfile: (name: string) => Promise<void>;
  /** P1 to P5: one Profiles write, sent once after a click-time config read still names its context. */
  readonly profileWrite: (command: ProfileWriteCommand) => Promise<void>;
  /**
   * F6R-04: read the battle an earlier live change was made to, by its id (a GET, never a
   * write); a record of that battle listing the change's key settles it.
   */
  readonly checkEarlierBattle: (intentId: string) => Promise<void>;
  /** The Collection page opened or closed: its settings, profile source and vocabulary are read while open. */
  readonly openCollection: (open: boolean) => void;
  /** C1 to C4 (and any settings module): one module save, sent once, never resent. */
  readonly saveSettings: (command: SettingsSaveCommand) => Promise<void>;
  /** C4 to C6: one profile patch, sent once after a click-time config read still names its context. */
  readonly patchProfile: (command: ProfilePatchCommand) => Promise<void>;
  readonly patchRunEntry: (command: RunEntryCommand) => Promise<void>;
  readonly checkRunEntry: () => void;
  /** Read the settings and the profile source again (a GET each, never a write). */
  readonly checkCollection: () => void;
  /** The Emulators page opened or closed: its preview settings are read, an install is followed. */
  readonly openEmulators: (open: boolean) => void;
  /** The Runtime page opened or closed: its settings are read while it is open. */
  readonly openRuntime: (open: boolean) => void;
  readonly openEquipment: (open: boolean) => void;
  /** The Recognition page opened or closed. */
  readonly openRecognition: (open: boolean) => void;
  /** R4: the run the Recognition page checks and prepares (null: none). Sends nothing. */
  readonly selectRecognitionRun: (run: string | null) => void;
  /** R4: read the chosen run's readiness now (GET /api/readiness?preset=); never polled. */
  readonly checkRecognitionReadiness: () => Promise<void>;
  /** R2: read the calibrator status (and the followed operation) again. Sends nothing. */
  readonly checkRecognition: () => void;
  /** R1, R5 to R8: one calibrator start or observe, after its click-time reads and checks. */
  readonly recognitionLaunch: (command: RecognitionLaunchCommand) => Promise<void>;
  /** R4: apply an operation's discoveries (or saved untracked observations) to the profile. */
  readonly recognitionApply: (command: RecognitionApplyCommand) => Promise<void>;
  /** R2, R8 (C6): ask the operation's worker to stop. */
  readonly recognitionStop: (command: RecognitionStopCommand) => Promise<void>;
  /** M2 to M5: one guarded emulator request, sent once, never resent. */
  readonly emulatorWrite: (command: EmulatorWriteCommand) => Promise<void>;
  /** M1: scan for emulators again (the backend's discovery; a GET, never a connection change). */
  readonly rescanEmulators: () => void;
  /** M5: read the connection tools state again (a GET). */
  readonly checkTools: () => void;
  /** M7, F6M-08: read the saved settings again (a GET; sends nothing). */
  readonly checkSettings: () => void;
  /** M6: read the configured address's screen size (a GET the backend answers by a capture). */
  readonly checkResolution: (serial: string) => Promise<void>;
}
