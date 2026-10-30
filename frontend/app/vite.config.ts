import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import {
  devBackend,
  devBackendProxy,
  realBackendTarget,
  REAL_BACKEND_ENV,
} from './scripts/dev-backend.ts';
import { buildInventory } from './scripts/build-inventory.ts';
import { htmlCopy } from './scripts/html-copy.ts';
import { licenceNotices } from './scripts/licence-notices.ts';

const realBackend = realBackendTarget(process.env[REAL_BACKEND_ENV]);
const proxy = devBackendProxy(realBackend);

export default defineConfig({
  // Flask serves frontend/webui/ at /ui/, so the build lives at /ui/app/.
  base: '/ui/app/',
  plugins: [react(), htmlCopy(), devBackend(realBackend), licenceNotices(), buildInventory()],
  build: {
    outDir: '../webui/app',
    emptyOutDir: true,
    sourcemap: false,
    assetsInlineLimit: 0,
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    ...(proxy === undefined ? {} : { proxy }),
  },
  preview: {
    host: '127.0.0.1',
  },
});
