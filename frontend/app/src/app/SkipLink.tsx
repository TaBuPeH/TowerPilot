import { copy } from '../copy/catalogue.ts';

/**
 * F-007: the first Tab stop. It moves focus to the page's main landmark
 * without changing the hash (the hash is the route).
 */
export function SkipLink() {
  return (
    <a
      className="tp-skip-link"
      href="#main"
      onClick={(event) => {
        event.preventDefault();
        document.getElementById('main')?.focus();
      }}
    >
      {copy.dashboard.shell.skipToContent.short}
    </a>
  );
}
