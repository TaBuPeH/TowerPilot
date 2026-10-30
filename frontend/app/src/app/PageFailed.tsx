import { copy } from '../copy/catalogue.ts';
import { RevocationFallback } from '../features/dashboard/shared/RevocationFallback.tsx';
import { Button } from '../ui/Button.tsx';

/**
 * F-107: the page itself failed to draw. It says so, offers Try again (draws
 * the page again; sends nothing), and still keeps Pause harvesting, Turn input
 * off and the known Stops (S-091), which read only the minimal fallback
 * records.
 */
export function PageFailed({ onRetry }: { readonly onRetry: () => void }) {
  return (
    <main id="main" className="tp-main" tabIndex={-1}>
      <h1 className="tp-page-title">{copy.dashboard.shell.pageName.short}</h1>
      <RevocationFallback part="page" withStops />
      <Button tier="secondary" label={copy.dashboard.shell.tryAgain.short} onClick={onRetry} />
    </main>
  );
}
