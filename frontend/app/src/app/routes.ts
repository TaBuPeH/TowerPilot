import type { ComponentType } from 'react';
import { copy } from '../copy/catalogue.ts';
import type { CopyEntry } from '../copy/text.ts';
import { DashboardPage } from '../features/dashboard/DashboardPage.tsx';
import { RunsPage } from '../features/runs/RunsPage.tsx';
import { ConnectedHistoryPage } from './history.ts';
import { history } from '../copy/history.ts';
import { schedule } from '../copy/schedule.ts';
import { ConnectedSchedulePage } from './schedule.ts';
import { ConnectedEquipmentPage } from './equipment.ts';
import { equipment } from '../copy/equipment.ts';
import { profiles } from '../copy/profiles.ts';
import { ProfilesPage } from '../features/profiles/ProfilesPage.tsx';
import type { RouteId, RouteTarget } from './router.ts';
import { ConnectedAbilitiesPage } from './abilities.ts';
import { abilities } from '../copy/abilities.ts';
import { collection } from '../copy/collection.ts';
import { CollectionPage } from '../features/collection/CollectionPage.tsx';
import { emulators } from '../copy/emulators.ts';
import { EmulatorsPage } from '../features/emulators/EmulatorsPage.tsx';
import { ConnectedRuntimePage } from './runtime.ts';
import { runtime } from '../copy/runtime.ts';
import { recognition } from '../copy/recognition.ts';
import { ConnectedRecognitionPage } from './recognition.ts';

export interface PageDefinition {
  /** The page's name, used for the tab title and the route announcement. */
  readonly title: CopyEntry;
  /** The page, given the route's target (null: none). */
  readonly Page: ComponentType<{ readonly target: RouteTarget | null }>;
}

export const PAGES: Readonly<Record<RouteId, PageDefinition>> = {
  dashboard: { title: copy.routes.dashboard, Page: DashboardPage },
  runs: { title: copy.routes.runs, Page: RunsPage },
  history: { title: history.pageName, Page: ConnectedHistoryPage },
  schedule: { title: schedule.pageName, Page: ConnectedSchedulePage },
  equipment: { title: equipment.pageName, Page: ConnectedEquipmentPage },
  profiles: { title: profiles.pageName, Page: ProfilesPage },
  abilities: { title: abilities.pageName, Page: ConnectedAbilitiesPage },
  collection: { title: collection.pageName, Page: CollectionPage },
  emulators: { title: emulators.pageName, Page: EmulatorsPage },
  runtime: { title: runtime.pageName, Page: ConnectedRuntimePage },
  recognition: { title: recognition.pageName, Page: ConnectedRecognitionPage },
};
