import { useEffect, useRef } from 'react';
import { copy } from '../../../copy/catalogue.ts';
import { wordChanges } from '../../../copy/dashboard/shell.ts';
import type { CopyText } from '../../../copy/text.ts';
import { selectControlWords, type ControlWordsVM } from '../../../store/selectors/controlHead.ts';
import { useAnnounce } from '../../../ui/LiveRegion.tsx';
import { useView } from '../context.ts';

const SLOTS = ['automation', 'input', 'gem'] as const;

/**
 * F-007, spec 8.2: when a Control slot's word changes, the one live region
 * says so once ("Gem harvesting: Unknown"). The first settled words are not
 * news (nothing is announced on load), an age tick changes no word, and a
 * failure repeated on every read is announced once, when it starts.
 */
export function ControlAnnouncer() {
  const words = useView(selectControlWords);
  const announce = useAnnounce();
  const previous = useRef<ControlWordsVM | null>(null);
  useEffect(() => {
    if (!words.settled) return;
    const before = previous.current;
    previous.current = words;
    if (before === null) return;
    const changes: [CopyText, CopyText][] = [];
    for (const slot of SLOTS) {
      const word = words[slot];
      if (word !== before[slot] && word !== null) {
        changes.push([copy.dashboard.shell.slots[slot].short, copy.states[word].short]);
      }
    }
    if (changes.length > 0) announce(wordChanges(changes));
  }, [words, announce]);
  return null;
}
