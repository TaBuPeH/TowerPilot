import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** Stable roots for test tooling, independent of a consumer's folder depth. */
export const APP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
export const SRC = path.join(APP, 'src');
export const REPO = path.resolve(APP, '../..');
