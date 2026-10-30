import { helpTip } from '../copy/helpTip.ts';
import type { CopyText } from '../copy/text.ts';
import { HelpTip } from './HelpTip.tsx';

export interface WhyProps {
  readonly subject?: CopyText;
  readonly why: CopyText | undefined;
}

/** Optional background for a visible line. Reasons, state and consequences stay in the line. */
export function Why({ subject = helpTip.message.short, why }: WhyProps) {
  if (why === undefined) {
    return null;
  }
  return <HelpTip label={subject}>{why}</HelpTip>;
}
