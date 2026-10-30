import { createContext, useCallback, useContext, useState } from 'react';
import type { CopyNode, CopyText } from '../copy/text.ts';

type Announce = (text: CopyText) => void;

const AnnounceContext = createContext<Announce | null>(null);

interface Message {
  readonly text: CopyText;
  readonly serial: number;
}

/**
 * The app's one live region (spec 8.2, C-8). Everything announced to screen
 * readers goes through `useAnnounce`, so announcements never compete across
 * regions; no other element carries a live role. Lives in ui/ so features may
 * announce; app/ mounts it.
 */
export function LiveRegionProvider({ children }: { readonly children: CopyNode }) {
  const [message, setMessage] = useState<Message | null>(null);
  const announce = useCallback<Announce>((text) => {
    setMessage((previous) => ({ text, serial: (previous?.serial ?? 0) + 1 }));
  }, []);
  return (
    <AnnounceContext value={announce}>
      {children}
      <div role="status" aria-live="polite" aria-atomic="true" className="tp-visually-hidden">
        {message === null ? null : <span key={message.serial}>{message.text}</span>}
      </div>
    </AnnounceContext>
  );
}

export function useAnnounce(): Announce {
  const announce = useContext(AnnounceContext);
  if (announce === null) {
    throw new Error('useAnnounce needs a LiveRegionProvider above it');
  }
  return announce;
}
