import { entry, made } from './entry.ts';

/** Shared list-control labels; field names and row values remain server text. */
export const settingsList = {
  add: entry('Add entry'),
  remove: entry('Remove entry'),
  malformed: entry('This saved entry is not text. Edit it or remove it.'),
  entry: (index: number) => made(`Entry ${String(index + 1)}`),
  listInvalid: entry('Each entry must be a non-blank string within the server’s list limit.'),
};
