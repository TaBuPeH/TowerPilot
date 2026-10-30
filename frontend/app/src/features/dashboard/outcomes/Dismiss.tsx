import { useId } from 'react';
import { copy } from '../../../copy/catalogue.ts';
import { Button } from '../../../ui/Button.tsx';

/** Every local dismissal explains that the original request is left alone (OD-10). */
export function Dismiss({ onClick }: { readonly onClick: () => void }) {
  const id = useId();
  const t = copy.dashboard.outcomes;
  return (
    <>
      <span id={id} className="tp-visually-hidden">
        {t.dismissDescription.short}
      </span>
      <Button tier="quiet" label={t.dismiss.short} describedBy={id} onClick={onClick} />
    </>
  );
}
