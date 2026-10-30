/**
 * The hash of each page of the new app and of a target on it (`#runs/live`,
 * `#runs/saved/<run>`): one definition, used by app/router.ts to match and by
 * features to link (features may not import app/).
 */
export const PAGE_IDS = [
  'dashboard',
  'runs',
  'history',
  'schedule',
  'equipment',
  'profiles',
  'abilities',
  'collection',
  'emulators',
  'runtime',
  'recognition',
] as const;

export type PageId = (typeof PAGE_IDS)[number];

/**
 * Where a page opens: the Runs page's live editor, one run's saved settings, or one
 * gathering policy of the Collection page.
 */
export type PageTarget =
  | { readonly kind: 'live' }
  | { readonly kind: 'saved'; readonly run: string }
  | { readonly kind: 'policy'; readonly policy: string }
  | {
      readonly kind: 'ability';
      readonly family: 'uw_policies' | 'rescue_policies';
      readonly policy: string;
    };

export function pageHref(id: PageId, target: PageTarget | null = null): `#${string}` {
  if (target === null) return `#${id}`;
  switch (target.kind) {
    case 'ability':
      return `#${id}/${target.family}/${encodeURIComponent(target.policy)}`;
    case 'live':
      return `#${id}/live`;
    case 'saved':
      return `#${id}/saved/${encodeURIComponent(target.run)}`;
    case 'policy':
      return `#${id}/policy/${encodeURIComponent(target.policy)}`;
  }
}
