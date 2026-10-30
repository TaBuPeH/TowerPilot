// @vitest-environment node
import { expect, it } from 'vitest';
import { selectPreviewReplay } from '../../support/fake-backend/preview-replay.mjs';
import { previewCases } from '../../support/previewFixture.ts';

it('PV3 PV4 PV5 fake replay matches recorded source/FPS/scale/quality and marks unsupported combinations', () => {
  for (const source of ['auto', 'mumu', 'scrcpy', 'adb']) {
    const binding = selectPreviewReplay(
      previewCases,
      new URL(
        `http://fixture/api/stream.mjpg?instance=main&stream_id=new-id&fps=15&scale=1&q=90&backend=${source}`,
      ),
      {},
    );
    expect(binding.requestMode).toBe('recorded');
    expect(binding.stream).toBe(`start_${source}`);
    expect(binding.status).toBe(`streaming_${source === 'auto' ? 'mumu' : source}`);
  }
  const balanced = selectPreviewReplay(
    previewCases,
    new URL(
      'http://fixture/api/stream.mjpg?instance=main&stream_id=new&fps=15&scale=0.65&q=85&backend=auto',
    ),
    {},
  );
  expect(balanced.stream).toBe('balanced');
  expect(balanced.status).toBe('status_after_balanced');
  expect(balanced.requestMode).toBe('recorded');
  const unsupported = selectPreviewReplay(
    previewCases,
    new URL(
      'http://fixture/api/stream.mjpg?instance=main&stream_id=new&fps=12.5&scale=0.65&q=85&backend=adb',
    ),
    {},
  );
  expect(unsupported.stream).toBe('start_adb');
  expect(unsupported.status).toBe('streaming_adb');
  expect(unsupported.requestMode).toBe('synthetic-request-plumbing');
  expect(unsupported.reason).not.toBeNull();
});
