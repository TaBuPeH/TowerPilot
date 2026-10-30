import { entry, made } from './entry.ts';

export const previewCopy = {
  title: entry('Screen preview'),
  show: entry('Show screen'),
  hide: entry('Hide screen'),
  viewOnly: entry(
    'View only; uses emulator capture and bandwidth while open.',
    'This picture does not confirm game recognition or permission to send input. It starts by itself while the emulator is online; Stop preview keeps it off until you start it.',
  ),
  start: entry('Start preview'),
  stop: entry('Stop preview'),
  reconnect: entry('Reconnect preview'),
  check: entry('Check availability'),
  checking: entry('Checking availability…'),
  checkBusy: entry('Stop preview before checking availability separately.'),
  offlineWaiting: entry('Emulator offline; the preview starts when it is back.'),
  offlineStopped: entry('Emulator offline. The preview stays stopped until you start it.'),
  availabilityFailed: entry('Availability could not be confirmed. Check again or open Emulators.'),
  transportFailed: entry('Preview delivery failed. Reconnect preview to try a new session.'),
  unknownDelivery: entry('Delivery cannot be confirmed from the current frame and status report.'),
  stale: entry('The displayed frame or server report is stale. Reconnect preview to try again.'),
  storageFailed: entry('Viewer preferences could not be saved in this browser.'),
  emulators: entry('Preview delivery settings on Emulators'),
  settings: entry('Viewer settings and diagnostics'),
  fps: entry('Target FPS'),
  source: entry('Requested source'),
  quality: entry('Picture quality'),
  saved: entry('Saved default; check to load'),
  useSaved: entry('Use saved FPS'),
  unknown: entry('unknown'),
  available: entry('Prerequisites available'),
  unavailable: entry('Prerequisites unavailable'),
  canvas: entry('Selected emulator screen, view only'),
  empty: entry('No preview picture'),
  sources: {
    auto: entry('Auto'),
    mumu: entry('MuMu native'),
    scrcpy: entry('scrcpy'),
    adb: entry('ADB'),
    unknown: entry('Unknown source'),
  },
  qualities: { native: entry('Native'), balanced: entry('Balanced'), low: entry('Low bandwidth') },
  verdicts: {
    off: entry('Preview off'),
    connection: entry('Connection not confirmed'),
    connecting: entry('Connecting'),
    receiving: entry('Receiving frames'),
    fallback: entry('Receiving via fallback'),
    stale: entry('Frame stale'),
    failed: entry('Preview failed'),
    unknown: entry('Delivery unknown'),
  },
  labels: {
    actual: entry('Displayed source: '),
    saved: entry('Saved source: '),
    requested: entry('Requested source'),
    target: entry('Target FPS'),
    effective: entry('Effective FPS'),
    rate: entry('Server FPS'),
    bytes: entry('Server JPEG bytes'),
    dimensions: entry('Server dimensions'),
    capture: entry('Server capture time'),
    encode: entry('Server encode time'),
    errors: entry('Server errors'),
    frames: entry('Server successful frames'),
    report: entry('Server report received'),
    serverAge: entry('Server completion age at report'),
  },
};
export const previewNumber = (value: number | null) =>
  value === null ? previewCopy.unknown.short : made(String(value));
export const previewFpsOption = (value: number) => made(`${value} FPS`);
export const previewSavedFps = (value: number | null) =>
  value === null ? previewCopy.saved.short : made(`Saved default: ${value} FPS`);
export const previewReceiptAge = (value: number | null) =>
  value === null
    ? made('Frame receipt age unknown')
    : made(`Frame received ${Math.round(value)} s ago`);
export const previewSourceAge = (value: number | null) =>
  value === null
    ? made('Source age unknown')
    : made(`Source age estimate at server encode: ${value} ms`);
export const previewReportAge = (value: number | null) =>
  value === null ? previewCopy.unknown.short : made(`${value.toFixed(1)} s ago`);
export const previewDimensions = (width: number | null, height: number | null) =>
  width === null || height === null ? previewCopy.unknown.short : made(`${width} × ${height}`);
export const previewMillis = (value: number | null) =>
  value === null ? previewCopy.unknown.short : made(`${value} ms`);
export const previewSeconds = (value: number | null) =>
  value === null ? previewCopy.unknown.short : made(`${value.toFixed(1)} s`);
