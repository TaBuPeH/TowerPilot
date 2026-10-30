import { useEffect, useId, useRef } from 'react';
import type { CopyNode, CopyText } from '../copy/text.ts';
import './ui.css';

export interface ConfirmDialogProps {
  readonly open: boolean;
  /** The action, as the dialog's title (catalogue text, or catalogue text around a named target). */
  readonly title: CopyNode;
  /** At most two sentences. */
  readonly body: CopyNode;
  readonly confirmLabel: CopyText;
  readonly cancelLabel: CopyText;
  /** halt for a Stop or kill; primary otherwise. */
  readonly tier: 'halt' | 'primary';
  readonly onConfirm: () => void;
  readonly onCancel: () => void;
}

/** The element with this focus key, when the opener itself is gone (spec 8.7). */
function byFocusKey(key: string | null): HTMLElement | null {
  if (key === null) return null;
  for (const element of document.querySelectorAll<HTMLElement>('[data-focus-key]')) {
    if (element.dataset['focusKey'] === key) return element;
  }
  return null;
}

/**
 * A confirmation (spec 3.5, 8.7): the native modal dialog. Focus starts on
 * Cancel and stays inside; Escape cancels; on close focus returns to the
 * opener, or to its replacement by `data-focus-key`.
 */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  cancelLabel,
  tier,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const opener = useRef<{ element: HTMLElement | null; key: string | null }>({
    element: null,
    key: null,
  });
  const titleId = useId();
  // The newest onCancel, so the listeners below stay registered for the dialog's whole life.
  const cancelHandler = useRef(onCancel);
  useEffect(() => {
    cancelHandler.current = onCancel;
  }, [onCancel]);

  useEffect(() => {
    const element = dialog.current;
    if (element === null) return;
    if (open && !element.open) {
      const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      opener.current = { element: active, key: active?.dataset['focusKey'] ?? null };
      element.showModal();
      cancel.current?.focus();
    } else if (!open && element.open) {
      element.close();
    }
  }, [open]);

  useEffect(() => {
    const element = dialog.current;
    if (element === null) return undefined;
    const onCancelEvent = (event: Event) => {
      event.preventDefault();
      cancelHandler.current();
    };
    const onClose = () => {
      const { element: was, key } = opener.current;
      const target = was?.isConnected === true ? was : byFocusKey(key);
      target?.focus();
    };
    element.addEventListener('cancel', onCancelEvent);
    element.addEventListener('close', onClose);
    return () => {
      element.removeEventListener('cancel', onCancelEvent);
      element.removeEventListener('close', onClose);
    };
  }, []);

  return (
    <dialog ref={dialog} className="tp-dialog" aria-labelledby={titleId}>
      <h2 id={titleId} className="tp-dialog__title">
        {title}
      </h2>
      <div className="tp-dialog__body">{body}</div>
      <div className="tp-dialog__actions">
        <button
          type="button"
          className="tp-button"
          data-tier={tier}
          onClick={() => {
            onConfirm();
          }}
        >
          {confirmLabel}
        </button>
        <button
          ref={cancel}
          type="button"
          className="tp-button"
          data-tier="secondary"
          onClick={() => {
            onCancel();
          }}
        >
          {cancelLabel}
        </button>
      </div>
    </dialog>
  );
}
