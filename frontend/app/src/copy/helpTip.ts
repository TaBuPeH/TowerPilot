import { entry, made } from './entry.ts';
import type { CopyText } from './text.ts';

/** The help icon (src/ui/HelpTip.tsx): the word its accessible name starts with. */
export const helpTip = {
  about: entry('About'),
  message: entry('this message'),
} as const;

/** The help icon's accessible name for a subject: "About Run speed". */
export function helpTipName(subject: CopyText): CopyText {
  return made(`${helpTip.about.short} ${subject}`);
}
