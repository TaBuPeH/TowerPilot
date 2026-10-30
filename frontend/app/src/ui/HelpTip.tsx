import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type {
  FocusEvent,
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent,
  PointerEvent,
} from 'react';
import { createPortal } from 'react-dom';
import { helpTip, helpTipName } from '../copy/helpTip.ts';
import type { CopyNode, CopyText } from '../copy/text.ts';
import './helpTip.css';

export interface HelpTipProps {
  /** The subject, a short catalogue name ("Run speed"); the icon's accessible name is "About <subject>". */
  readonly label: CopyText;
  /** Optional existing subject label, including server text rendered by ServerValue. */
  readonly labelledBy?: string | undefined;
  /** The background: how it works, why it is so. Text and plain elements, never a link or a control. */
  readonly children: CopyNode;
}

/** How long the pointer may be off both icon and tip before the tip closes: time to cross the gap. */
const LEAVE_DELAY_MS = 150;
/** Space between the icon and the tip, and the least space kept to the viewport edge. */
const GAP_PX = 4;
const EDGE_PX = 8;

/** Why the tip is open; it is open while any one holds. */
interface Causes {
  readonly hover: boolean;
  readonly focus: boolean;
  /** A click or tap pinned it: it stays until dismissed. */
  readonly pinned: boolean;
}
const NONE: Causes = { hover: false, focus: false, pinned: false };

/** Centre the tip under the icon (above it when there is no room below), inside the viewport. */
function placeTip(trigger: HTMLElement, tip: HTMLElement): void {
  const box = trigger.getBoundingClientRect();
  const viewWidth = document.documentElement.clientWidth;
  const viewHeight = window.innerHeight;
  const width = tip.offsetWidth;
  const height = tip.offsetHeight;
  const left = Math.min(
    Math.max(box.left + box.width / 2 - width / 2, EDGE_PX),
    Math.max(EDGE_PX, viewWidth - width - EDGE_PX),
  );
  const below = box.bottom + GAP_PX;
  const above = box.top - GAP_PX - height;
  const fitsBelow = below + height + EDGE_PX <= viewHeight;
  const wanted = fitsBelow || above < EDGE_PX ? below : above;
  const top = Math.min(Math.max(wanted, EDGE_PX), Math.max(EDGE_PX, viewHeight - height - EDGE_PX));
  tip.style.left = `${String(left)}px`;
  tip.style.top = `${String(top)}px`;
  // Overflowing help is also a native keyboard scroll target; short tips add no tab stop.
  tip.tabIndex = tip.scrollHeight > tip.clientHeight ? 0 : -1;
}

/**
 * Background on demand beside a heading or field: a small info icon whose tip opens
 * on hover, keyboard focus, click or tap and closes on Escape or a press outside.
 * The tip stays open while the pointer is on it (WCAG 1.4.13), is described to the
 * icon with aria-describedby, and draws in a portal so no ancestor's overflow or
 * containment clips it. The icon reserves its full hit area in the layout.
 *
 * Use it for explanation only. A reason an action is unavailable, an error, a
 * status or what an action will do stays visible in the page, never here. Do not
 * put it inside a button, a link or a `summary` (nested interactive content), and
 * keep links out of the tip: it closes when focus or the pointer leaves it.
 */
export function HelpTip({ label, labelledBy, children }: HelpTipProps) {
  const tipId = useId();
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const leaveTimer = useRef<number | undefined>(undefined);
  /** Set by a pointer press so the focus it causes does not open the tip; keyboard focus does. */
  const pressed = useRef(false);
  /** Where the tip draws: the nearest open dialog (top layer), else the page body. */
  const [host, setHost] = useState<Element | null>(null);
  const [causes, setCauses] = useState<Causes>(NONE);
  const open = causes.hover || causes.focus || causes.pinned;

  const bind = useCallback((node: HTMLButtonElement | null) => {
    triggerRef.current = node;
    if (node !== null) {
      setHost(node.closest('dialog') ?? node.ownerDocument.body);
    }
  }, []);

  useEffect(
    () => () => {
      window.clearTimeout(leaveTimer.current);
    },
    [],
  );

  // Escape and a press outside both dismiss; neither needs the pointer to move.
  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        window.clearTimeout(leaveTimer.current);
        if (tipRef.current?.contains(document.activeElement) === true) {
          triggerRef.current?.focus({ preventScroll: true });
        }
        setCauses(NONE);
      }
    };
    const onPress = (event: Event) => {
      const target = event.target;
      if (
        target instanceof Node &&
        (triggerRef.current?.contains(target) === true || tipRef.current?.contains(target) === true)
      ) {
        return;
      }
      window.clearTimeout(leaveTimer.current);
      setCauses(NONE);
    };
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', onPress, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('pointerdown', onPress, true);
    };
  }, [open]);

  // Keep the tip on screen while it is open and the page moves under it.
  useLayoutEffect(() => {
    if (!open) {
      return undefined;
    }
    const place = () => {
      if (triggerRef.current !== null && tipRef.current !== null) {
        placeTip(triggerRef.current, tipRef.current);
      }
    };
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    const watcher =
      typeof ResizeObserver === 'undefined' || tipRef.current === null
        ? undefined
        : new ResizeObserver(place);
    if (watcher !== undefined && tipRef.current !== null) {
      watcher.observe(tipRef.current);
    }
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      watcher?.disconnect();
    };
  }, [open, host]);

  const hoverOn = (event: PointerEvent) => {
    if (event.pointerType === 'touch') {
      return;
    }
    window.clearTimeout(leaveTimer.current);
    setCauses((now) => ({ ...now, hover: true }));
  };
  const hoverOff = (event: PointerEvent) => {
    if (event.pointerType === 'touch') {
      return;
    }
    window.clearTimeout(leaveTimer.current);
    leaveTimer.current = window.setTimeout(() => {
      setCauses((now) => ({ ...now, hover: false }));
    }, LEAVE_DELAY_MS);
  };
  const focusOn = () => {
    if (!pressed.current) {
      setCauses((now) => ({ ...now, focus: true }));
    }
  };
  const focusOff = (event: FocusEvent<HTMLElement>) => {
    pressed.current = false;
    const next = event.relatedTarget;
    if (
      next instanceof Node &&
      (next === triggerRef.current || tipRef.current?.contains(next) === true)
    ) {
      return;
    }
    // Focus moved to another control: a pinned tip goes with it. Focus left the page: it stays.
    setCauses((now) => ({ ...now, focus: false, pinned: next === null ? now.pinned : false }));
  };
  const toggle = (event: MouseEvent<HTMLButtonElement>) => {
    // The icon explains; it never submits a form, follows a link or acts for a parent.
    event.preventDefault();
    event.stopPropagation();
    pressed.current = false;
    window.clearTimeout(leaveTimer.current);
    // A key press (detail 0) closes an open tip; a click or tap pins it, and closes it when pinned.
    setCauses((now) => {
      const isOpen = now.hover || now.focus || now.pinned;
      return now.pinned || (event.detail === 0 && isOpen) ? NONE : { ...now, pinned: true };
    });
  };

  // Keep focus on the icon while a keyboard user reads help taller than the viewport.
  const scrollTip = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    const tip = tipRef.current;
    if (!open || tip === null || tip.scrollHeight <= tip.clientHeight) return;
    let top: number;
    switch (event.key) {
      case 'ArrowDown':
        top = tip.scrollTop + Number.parseFloat(getComputedStyle(tip).lineHeight);
        break;
      case 'ArrowUp':
        top = tip.scrollTop - Number.parseFloat(getComputedStyle(tip).lineHeight);
        break;
      case 'PageDown':
        top = tip.scrollTop + tip.clientHeight;
        break;
      case 'PageUp':
        top = tip.scrollTop - tip.clientHeight;
        break;
      case 'Home':
        top = 0;
        break;
      case 'End':
        top = tip.scrollHeight;
        break;
      default:
        return;
    }
    event.preventDefault();
    event.stopPropagation();
    tip.scrollTop = top;
  };

  // The tip is drawn in a portal, but React still bubbles its events to the icon's parents.
  const keepHere = (event: MouseEvent) => {
    event.stopPropagation();
  };

  return (
    <>
      <button
        ref={bind}
        type="button"
        className="tp-help__trigger"
        aria-label={labelledBy === undefined ? helpTipName(label) : undefined}
        aria-labelledby={labelledBy === undefined ? undefined : `${tipId}-about ${labelledBy}`}
        aria-expanded={open}
        aria-controls={open ? tipId : undefined}
        aria-describedby={open ? tipId : undefined}
        onPointerDown={() => {
          pressed.current = true;
        }}
        onPointerEnter={hoverOn}
        onPointerLeave={hoverOff}
        onFocus={focusOn}
        onBlur={focusOff}
        onClick={toggle}
        onKeyDown={scrollTip}
      >
        {labelledBy === undefined ? null : (
          <span id={`${tipId}-about`} className="tp-visually-hidden">
            {helpTip.about.short}
          </span>
        )}
        <svg className="tp-help__glyph" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <circle cx="8" cy="8" r="6.6" />
          <line x1="8" y1="7.2" x2="8" y2="11.4" />
          <circle cx="8" cy="4.9" r="0.6" className="tp-help__dot" />
        </svg>
      </button>
      {open && host !== null
        ? createPortal(
            // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions -- onClick only stops bubbling to the icon's parents; the tip does nothing on click.
            <div
              ref={tipRef}
              id={tipId}
              role="tooltip"
              tabIndex={-1}
              className="tp-help__tip"
              onPointerEnter={hoverOn}
              onPointerLeave={hoverOff}
              onClick={keepHere}
              onFocus={focusOn}
              onBlur={focusOff}
            >
              {children}
            </div>,
            host,
          )
        : null}
    </>
  );
}
