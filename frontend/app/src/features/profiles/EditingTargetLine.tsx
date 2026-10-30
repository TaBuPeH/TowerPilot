import { editingTarget as t } from '../../copy/editingTarget.ts';
import {
  editingSourceError,
  editingTargetVM,
  selectEditingTarget,
} from '../../store/selectors/editingTarget.ts';
import { TargetSourceError } from './TargetSourceError.tsx';
import { Fact, Facts } from '../../ui/Facts.tsx';
import { ServerValue } from '../../ui/ServerValue.tsx';
import { useView } from '../dashboard/context.ts';

/** Shared editor heading; target and permission come from the store's one projection. */
export function EditingTargetLine({ inPicker = false }: { readonly inPicker?: boolean }) {
  const target = useView(selectEditingTarget);
  const error = useView(editingSourceError);
  const vm = editingTargetVM(target);
  return (
    <div
      className="tp-profiles__scope"
      data-editing-target={target.name ?? ''}
      data-target-blocked={target.blocked ?? ''}
    >
      <Facts>
        <Fact
          label={target.inactive ? t.editing.short : t.following.short}
          value={
            <>
              {vm.name === null ? t.none.short : <ServerValue kind="name" value={vm.name} />}
              {target.inactive ? <span>{t.inactive.short}</span> : null}
            </>
          }
        />
        {inPicker || !target.inactive ? null : (
          <Fact
            label={t.active.short}
            value={
              vm.active === null ? t.none.short : <ServerValue kind="name" value={vm.active} />
            }
          />
        )}
      </Facts>
      {target.blocked === null ? null : (
        <div className="tp-reason">{t.blocked[target.blocked].short}</div>
      )}
      {inPicker ? null : <a href="#profiles">{t.change.short}</a>}
      <TargetSourceError error={error} />
    </div>
  );
}
