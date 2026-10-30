import { useEffect, useRef, useState } from 'react';
import { copy } from '../../../copy/catalogue.ts';
import { selectToasts, type ToastVM } from '../../../store/selectors/toasts.ts';
import { Button } from '../../../ui/Button.tsx';
import { useAnnounce } from '../../../ui/LiveRegion.tsx';
import { useView } from '../context.ts';

const text = copy.dashboard.shell.toasts;

/** How long a toast stays at least (F-107), counted only while it is not hovered or focused. */
export const TOAST_MS = 8_000;

function Toast({ toast, onDone }: { readonly toast: ToastVM; readonly onDone: () => void }) {
  const [paused, setPaused] = useState(false);
  const left = useRef(TOAST_MS);
  const started = useRef(0);
  const item = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (paused) return undefined;
    started.current = Date.now();
    const timer = window.setTimeout(onDone, left.current);
    return () => {
      window.clearTimeout(timer);
      left.current -= Date.now() - started.current;
    };
  }, [paused, onDone]);
  // Hovering or focusing the toast pauses its timer (F-107).
  useEffect(() => {
    const element = item.current;
    if (element === null) return undefined;
    const pause = () => {
      setPaused(true);
    };
    const resume = () => {
      setPaused(false);
    };
    element.addEventListener('mouseenter', pause);
    element.addEventListener('mouseleave', resume);
    element.addEventListener('focusin', pause);
    element.addEventListener('focusout', resume);
    return () => {
      element.removeEventListener('mouseenter', pause);
      element.removeEventListener('mouseleave', resume);
      element.removeEventListener('focusin', pause);
      element.removeEventListener('focusout', resume);
    };
  }, []);
  return (
    <li ref={item} className="tp-toast" data-toast={toast.kind}>
      <span>{text[toast.kind].short}</span>
      <Button tier="quiet" label={text.dismiss.short} onClick={onDone} />
    </li>
  );
}

/**
 * F-107: applied low-stakes results, echoed for at least 8 s (paused while
 * hovered or focused) and announced once through the one live region. Only
 * answers that arrive while the page is open toast; nothing is sent.
 */
export function Toasts() {
  const toasts = useView(selectToasts);
  const announce = useAnnounce();
  const seen = useRef<Set<string> | null>(null);
  const [shown, setShown] = useState<readonly ToastVM[]>([]);
  useEffect(() => {
    const key = (toast: ToastVM) => `${toast.id}:${String(toast.answerSeq)}`;
    if (seen.current === null) {
      // Answers that were already there when the page opened are not news.
      seen.current = new Set(toasts.map(key));
      return;
    }
    const known = seen.current;
    const fresh = toasts.filter((toast) => !known.has(key(toast)));
    if (fresh.length === 0) return;
    for (const toast of fresh) {
      known.add(key(toast));
      announce(text[toast.kind].short);
    }
    setShown((current) => [...current, ...fresh]);
  }, [toasts, announce]);
  if (shown.length === 0) return null;
  return (
    <ul className="tp-toasts">
      {shown.map((toast) => (
        <Toast
          key={`${toast.id}:${String(toast.answerSeq)}`}
          toast={toast}
          onDone={() => {
            setShown((current) => current.filter((entry) => entry !== toast));
          }}
        />
      ))}
    </ul>
  );
}
