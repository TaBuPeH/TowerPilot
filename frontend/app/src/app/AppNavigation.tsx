import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';
import { copy } from '../copy/catalogue.ts';
import { pageHref, type PageId } from '../ui/routes.ts';

const words = copy.dashboard.topBar;
const wideQuery = '(min-width: 1000px)';

function subscribe(onChange: () => void) {
  if (typeof window.matchMedia !== 'function') return () => {};
  const media = window.matchMedia(wideQuery);
  media.addEventListener('change', onChange);
  return () => {
    media.removeEventListener('change', onChange);
  };
}

function isWide() {
  return typeof window.matchMedia === 'function' && window.matchMedia(wideQuery).matches;
}

/** App navigation survives routed-page failures and never covers page controls. */
export function AppNavigation({ currentPage }: { readonly currentPage: PageId | null }) {
  const wide = useSyncExternalStore(subscribe, isWide, () => false);
  const [expanded, setExpanded] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const navigation = useRef<HTMLElement>(null);
  const focused = useRef<HTMLElement | null>(null);

  useLayoutEffect(() => {
    if (wide && focused.current === trigger.current) {
      const destination =
        navigation.current?.querySelector<HTMLElement>('[aria-current="page"]') ??
        navigation.current?.querySelector<HTMLElement>('a[href]');
      destination?.focus();
    } else if (!wide && !expanded && navigation.current?.contains(focused.current)) {
      trigger.current?.focus();
    }
  }, [wide, expanded]);

  useEffect(() => {
    function closeOnRouteChange() {
      if (!wide && navigation.current?.contains(document.activeElement)) {
        trigger.current?.focus();
      }
      setExpanded(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (
        !wide &&
        event.key === 'Escape' &&
        (navigation.current?.contains(document.activeElement) ||
          document.activeElement === trigger.current)
      ) {
        event.preventDefault();
        setExpanded(false);
        trigger.current?.focus();
      }
    }
    window.addEventListener('hashchange', closeOnRouteChange);
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      window.removeEventListener('hashchange', closeOnRouteChange);
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [wide]);

  return (
    <aside
      className="tp-app-navigation"
      aria-label={copy.app.name.short}
      onFocusCapture={(event) => {
        focused.current = event.target;
      }}
      onBlurCapture={(event) => {
        // Hiding the focused element can blur it before breakpoint recovery runs.
        if (event.target.closest('[hidden]') === null) focused.current = null;
      }}
    >
      <div className="tp-app-navigation__identity">
        <span className="tp-brand">{copy.app.name.short}</span>
        <button
          className="tp-app-navigation__toggle"
          type="button"
          ref={trigger}
          aria-expanded={expanded}
          aria-controls="app-navigation"
          hidden={wide}
          onClick={() => {
            setExpanded((value) => !value);
          }}
        >
          {words.navigation.short}
        </button>
      </div>
      <nav
        id="app-navigation"
        ref={navigation}
        aria-label={words.navigation.short}
        hidden={!wide && !expanded}
      >
        {words.groups.map((group) => (
          <div className="tp-app-navigation__group" key={group.id}>
            <h2>{group.label.short}</h2>
            <ul>
              {words.appPages
                .filter((page) => page.group === group.id)
                .map((page) => (
                  <li key={page.id}>
                    <a
                      href={pageHref(page.id)}
                      aria-current={page.id === currentPage ? 'page' : undefined}
                      onClick={() => {
                        if (!wide) {
                          setExpanded(false);
                          trigger.current?.focus();
                        }
                      }}
                    >
                      {page.label.short}
                    </a>
                  </li>
                ))}
            </ul>
          </div>
        ))}
      </nav>
      <p className="tp-app-navigation__hint" hidden={wide || !expanded}>
        {words.navigationScroll.short}
      </p>
    </aside>
  );
}
