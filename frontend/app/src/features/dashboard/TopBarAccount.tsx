import { copy } from '../../copy/catalogue.ts';
import type { TopBarVM } from '../../store/selectors/topBar.ts';
import { ServerValue } from '../../ui/ServerValue.tsx';

/**
 * The account slice of the top bar: selectors supply facts and reason codes;
 * this feature chooses catalogue wording and gives server text to ServerValue.
 */
export function TopBarAccount({ vm }: { readonly vm: TopBarVM }) {
  if (vm.account.kind === 'unknown') {
    return (
      <span>
        {vm.account.reason === 'no_active_account'
          ? copy.dashboard.topbar.noAccount.short
          : copy.dashboard.topbar.accountUnknown.short}
      </span>
    );
  }
  // The section label names the account; the value stands alone.
  return <ServerValue value={vm.account.value} kind="name" />;
}
