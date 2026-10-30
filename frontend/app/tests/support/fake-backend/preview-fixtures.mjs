// @ts-check
/** Preview protocol recordings with neutral test-only JPEGs; no game pixels. */
import { encode } from 'jpeg-js';
import { record } from '../../../scripts/generate-contract.mjs';
import { selectPreviewReplay } from './preview-replay.mjs';

/** @typedef {{ availability?: string, status?: string, stream?: string, fault?: 'split'|'stall'|'truncate'|'oversized'|'malformed'|'close'|'delay' }} PreviewScenario */
/** @type {Readonly<Record<string, import('./scenarios.mjs').Scenario>>} */
export const PREVIEW_SCENARIOS = Object.freeze({
  preview_fallback_adb: {
    description: 'Recorded Auto fallback through scrcpy to ADB',
    harvest: 'off',
    preview: { stream: 'fallback_mumu_scrcpy_to_adb', status: 'fallback_mumu_scrcpy_to_adb' },
  },
  preview_fallback_scrcpy_adb: {
    description: 'Recorded requested scrcpy fallback to ADB',
    harvest: 'off',
    preview: { stream: 'fallback_scrcpy_to_adb', status: 'fallback_scrcpy_to_adb' },
  },
  preview_fallback_requested_mumu: {
    description: 'Recorded requested MuMu fallback to scrcpy',
    harvest: 'off',
    preview: {
      stream: 'requested_mumu_falls_back_to_scrcpy',
      status: 'requested_mumu_falls_back_to_scrcpy',
    },
  },
  preview_recovered: {
    description: 'Recorded recovery after error marker',
    harvest: 'off',
    preview: { status: 'error_marker_status_after_recovery' },
  },
  preview_both_missing: {
    description: 'Recorded package and decoder missing',
    harvest: 'off',
    preview: { availability: 'scrcpy_package_and_decoder_missing' },
  },
  preview_owned: {
    description: 'Recorded view-only stream while automation owns the screen',
    harvest: 'owned_by_run_gathers',
    preview: { stream: 'start_while_automation_owns_screen', status: 'streaming_mumu' },
  },
  preview: {
    description: 'Recorded Balanced preview, explicit consent',
    harvest: 'off',
    preview: {},
  },
  preview_fallback: {
    description: 'Recorded accelerated fallback',
    harvest: 'off',
    preview: { stream: 'fallback_mumu_to_scrcpy', status: 'fallback_mumu_to_scrcpy' },
  },
  preview_adb: {
    description: 'Recorded ADB cap',
    harvest: 'off',
    preview: { stream: 'start_adb', status: 'streaming_adb' },
  },
  preview_failed: {
    description: 'Recorded capture failure',
    harvest: 'off',
    preview: { status: 'failed_mumu' },
  },
  preview_stale: {
    description: 'Recorded stale server report',
    harvest: 'off',
    preview: { status: 'stale_mumu' },
  },
  preview_idle: {
    description: 'Reserved session before pixels',
    harvest: 'off',
    preview: { status: 'idle_reserved', fault: 'stall' },
  },
  preview_first: {
    description: 'Recorded first successful JPEG, unknown rate',
    harvest: 'off',
    preview: { status: 'first_frame_mumu', fault: 'stall' },
  },
  preview_marker: {
    description: 'Recorded error marker and recovery',
    harvest: 'off',
    preview: {
      stream: 'error_marker_then_recovery',
      status: 'error_marker_status_before_recovery',
      fault: 'delay',
    },
  },
  preview_closed: {
    description: 'Recorded ended session',
    harvest: 'off',
    preview: { status: 'closed_mumu' },
  },
  preview_cleanup: {
    description: 'Recorded cleanup error',
    harvest: 'off',
    preview: { status: 'cleanup_failed' },
  },
  preview_404: {
    description: 'Recorded evicted session',
    harvest: 'off',
    preview: { status: 'evicted_session' },
  },
  preview_package: {
    description: 'Recorded missing scrcpy package',
    harvest: 'off',
    preview: { availability: 'scrcpy_package_missing' },
  },
  preview_decoder: {
    description: 'Recorded missing decoder',
    harvest: 'off',
    preview: { availability: 'scrcpy_decoder_missing' },
  },
  preview_unavailable: {
    description: 'Recorded unavailable MuMu',
    harvest: 'off',
    preview: { availability: 'mumu_unavailable' },
  },
  preview_bad_settings: {
    description: 'Recorded invalid saved FPS',
    harvest: 'off',
    preview: { availability: 'invalid_saved_fps' },
  },
  preview_saved: {
    description: 'Recorded saved scrcpy and fractional FPS',
    harvest: 'off',
    preview: { availability: 'saved_scrcpy_fractional_fps' },
  },
  // Synthetic transport faults below leave recorded JSON and part identity unchanged.
  preview_split: {
    description: 'Split/delayed boundaries and short reads',
    harvest: 'off',
    preview: { fault: 'split' },
  },
  preview_truncate: {
    description: 'A network cut inside a JPEG',
    harvest: 'off',
    preview: { fault: 'truncate' },
  },
  preview_oversized: {
    description: 'Corrupted Content-Length exercises the memory bound',
    harvest: 'off',
    preview: { fault: 'oversized' },
  },
  preview_malformed: {
    description: 'Non-numeric Content-Length exercises validation',
    harvest: 'off',
    preview: { fault: 'malformed' },
  },
  preview_close: {
    description: 'Transport closes after two complete parts',
    harvest: 'off',
    preview: { fault: 'close' },
  },
});

/** @param {unknown} cases @param {string} family @param {string} name */
export function previewCase(cases, family, name) {
  const value = record(record(cases)[family])[name];
  if (value === undefined) throw new Error(`No recorded ${family}.${name}`);
  return record(value);
}
/** @type {Map<string, Buffer>} */
const neutralCache = new Map();
/** Generate a neutral JPEG from recorded dimensions, padding with JPEG comments to its recorded size.
 * Pixel hashes in recordings bind the backend encoder only, never this test encoder.
 * @param {number} width @param {number} height @param {number | undefined} size */
export function neutralPreviewJpeg(width, height, size) {
  const key = `${width}/${height}/${size}`;
  const found = neutralCache.get(key);
  if (found !== undefined) return found;
  const pixels = Buffer.alloc(width * height * 4, 128);
  for (let i = 3; i < pixels.length; i += 4) pixels[i] = 255;
  const encoded = Buffer.from(encode({ data: pixels, width, height }, 70).data);
  if (size === undefined) {
    neutralCache.set(key, encoded);
    return encoded;
  }
  if (encoded.length > size || (size - encoded.length > 0 && size - encoded.length < 4))
    throw new Error('Neutral encoder exceeds recorded JPEG budget');
  const comments = [];
  let remaining = size - encoded.length;
  while (remaining > 0) {
    let count = Math.min(65537, remaining);
    if (remaining - count > 0 && remaining - count < 4) count -= 4;
    const comment = Buffer.alloc(count, 32);
    comment[0] = 255;
    comment[1] = 254;
    comment.writeUInt16BE(count - 2, 2);
    comments.push(comment);
    remaining -= count;
  }
  const result = Buffer.concat([encoded.subarray(0, -2), ...comments, encoded.subarray(-2)]);
  neutralCache.set(key, result);
  return result;
}
/** @param {unknown} part */
export function previewPartBytes(part) {
  const row = record(part);
  const binary = record(row['binary']);
  const headers = record(row['headers']);
  const bytes = neutralPreviewJpeg(
    Number(binary['width']),
    Number(binary['height']),
    typeof binary['encoded_bytes'] === 'number' ? binary['encoded_bytes'] : undefined,
  );
  return Buffer.concat([
    Buffer.from(
      `--frame\r\n${Object.entries({ ...headers, 'Content-Length': String(bytes.length) })
        .map(([key, value]) => `${key}: ${value}`)
        .join('\r\n')}\r\n\r\n`,
    ),
    bytes,
    Buffer.from('\r\n'),
  ]);
}
/** @param {import('node:http').IncomingMessage} req @param {import('node:http').ServerResponse} res @param {URL} url @param {unknown} cases @param {PreviewScenario} scenario @param {import('./preview-replay.mjs').PreviewFixtureState} state */
export function servePreview(req, res, url, cases, scenario, state) {
  if (!['/api/stream/backends', '/api/stream/status', '/api/stream.mjpg'].includes(url.pathname))
    return false;
  if (url.pathname !== '/api/stream.mjpg') {
    const isStatus = url.pathname === '/api/stream/status';
    const answer = previewCase(
      cases,
      isStatus ? 'preview_status' : 'preview_backends',
      isStatus
        ? (scenario.status ??
            state.streams.get(url.searchParams.get('stream_id') ?? '')?.status ??
            'missing_session')
        : (scenario.availability ?? 'all_available'),
    );
    let body = record(answer['body']);
    // Correlation-only rebinding: browser sessions have fresh IDs; every other recorded field stays exact.
    if (isStatus && typeof body['stream_id'] === 'string')
      body = { ...body, stream_id: url.searchParams.get('stream_id') };
    res.writeHead(
      Number(answer['status']),
      /** @type {Record<string,string>} */ (answer['headers']),
    );
    res.end(JSON.stringify(body));
    return true;
  }
  const binding = selectPreviewReplay(cases, url, scenario);
  const id = url.searchParams.get('stream_id') ?? '';
  const answer = previewCase(cases, 'preview_stream', binding.stream);
  res.setHeader('X-Preview-Fixture-Mode', binding.requestMode);
  res.writeHead(Number(answer['status']), /** @type {Record<string,string>} */ (answer['headers']));
  if (Number(answer['status']) !== 200) {
    res.end(JSON.stringify(answer['body']));
    return true;
  }
  if (req.method === 'HEAD') {
    res.end();
    return true;
  }
  state.streams.set(id, binding);
  while (state.streams.size > 32) {
    const first = state.streams.keys().next().value;
    if (first === undefined) break;
    state.streams.delete(first);
  }
  state.active.add(id);
  state.maxActive = Math.max(state.maxActive, state.active.size);
  res.on('close', () => {
    state.active.delete(id);
  });
  res.flushHeaders();
  // The recorded reserved status precedes iteration: stall before the first part, not after it.
  if (scenario.status === 'idle_reserved') return true;
  const parts = record(record(answer['body'])['multipart'])['parts'];
  if (!Array.isArray(parts)) throw new Error('No recorded multipart parts');
  /** @type {Set<ReturnType<typeof setTimeout>>} */
  const timers = new Set();
  /** @param {() => void} action @param {number} ms */
  const later = (action, ms) => {
    const timer = setTimeout(() => {
      timers.delete(timer);
      if (!res.destroyed) action();
    }, ms);
    timers.add(timer);
  };
  res.on('close', () => {
    for (const timer of timers) clearTimeout(timer);
    timers.clear();
  });
  parts.forEach((part, index) => {
    if (scenario.fault === 'stall' && index > 0) return;
    later(
      () => {
        let bytes = previewPartBytes(part);
        // Faults change the transport only, deliberately proving bounded rejection and no blind restart.
        if (scenario.fault === 'oversized' || scenario.fault === 'malformed')
          bytes = Buffer.from(
            bytes
              .toString('latin1')
              .replace(
                /Content-Length: \d+/,
                `Content-Length: ${scenario.fault === 'oversized' ? '999999999' : 'wat'}`,
              ),
            'latin1',
          );
        if (scenario.fault === 'truncate') {
          res.end(bytes.subarray(0, -10));
          return;
        }
        if (scenario.fault === 'split') {
          res.write(bytes.subarray(0, 3));
          later(() => res.write(bytes.subarray(3, 11)), 15);
          later(() => res.write(bytes.subarray(11, 201)), 30);
          later(() => res.write(bytes.subarray(201)), 45);
        } else res.write(bytes);
        if (scenario.fault === 'close' && index === parts.length - 1) later(() => res.end(), 25);
      },
      index === 0 ? 20 : scenario.fault === 'delay' ? 5000 : 220,
    );
  });
  // Recorded finite samples stop advancing after their second part; keep the response open until abort.
  return true;
}
