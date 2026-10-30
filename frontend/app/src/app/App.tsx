import { useEffect, useRef, useState } from 'react';
import { copy, documentTitle } from '../copy/catalogue.ts';
import type { CopyEntry } from '../copy/text.ts';
import { LiveRegionProvider, useAnnounce } from '../ui/LiveRegion.tsx';
import { RegionBoundary } from '../ui/RegionBoundary.tsx';
import { DashboardProvider, readFaults } from './DashboardProvider.tsx';
import { NotFoundPage } from './NotFoundPage.tsx';
import { PageFailed } from './PageFailed.tsx';
import { useRoute, type RouteMatch } from './router.ts';
import { PAGES } from './routes.ts';
import { SkipLink } from './SkipLink.tsx';
import type { FaultSet } from '../features/dashboard/context.ts';
import { createDashboardData, type DashboardData } from './wiring.ts';
import { AppNavigation } from './AppNavigation.tsx';
import './appShell.css';

/** Sets the tab title and announces a page change (not the first page) in the live region. */
function PageChange({ title }: { readonly title: CopyEntry }) {
  const announce = useAnnounce();
  const previous = useRef<CopyEntry | null>(null);
  useEffect(() => {
    document.title = documentTitle(title);
    if (previous.current !== null && previous.current !== title) {
      announce(title.short);
    }
    previous.current = title;
  }, [announce, title]);
  return null;
}

function RouteView({ route }: { readonly route: RouteMatch }) {
  if (route.kind === 'not_found') {
    return (
      <>
        <main id="main" className="tp-main" tabIndex={-1}>
          <NotFoundPage />
        </main>
      </>
    );
  }
  const { Page } = PAGES[route.id];
  return <Page target={route.target} />;
}

export interface AppProps {
  /** The page's data layer; the real page makes its own (tests pass one on a fake transport). */
  readonly data?: DashboardData;
  /** Parts made to fail while drawing (tests only); the real page reads the session key. */
  readonly faults?: FaultSet;
}

export function App({ data: given, faults: givenFaults }: AppProps = {}) {
  const route = useRoute();
  const [data] = useState(() => given ?? createDashboardData());
  const [faults] = useState(() => givenFaults ?? readFaults());
  const [attempt, setAttempt] = useState(0);
  const title = route.kind === 'page' ? PAGES[route.id].title : copy.routes.notFound;
  const resetKey = `${route.kind === 'page' ? route.id : route.hash}:${String(attempt)}`;
  return (
    <LiveRegionProvider>
      <PageChange title={title} />
      <DashboardProvider data={data} faults={faults}>
        <SkipLink />
        <div className="tp-app-shell">
          <AppNavigation currentPage={route.kind === 'page' ? route.id : null} />
          <div className="tp-workspace">
            <RegionBoundary
              resetKey={resetKey}
              fallback={
                <PageFailed
                  onRetry={() => {
                    setAttempt((value) => value + 1);
                  }}
                />
              }
              onError={(error) => {
                data.diagnostics.report(`render:page:${String(error)}`, {
                  kind: 'javascript_error',
                  page: 'dashboard',
                  message: `page: ${String(error)}`,
                });
              }}
            >
              <RouteView route={route} />
            </RegionBoundary>
          </div>
        </div>
      </DashboardProvider>
    </LiveRegionProvider>
  );
}
