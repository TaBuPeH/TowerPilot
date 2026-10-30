import { entries, list, own, ownPath, text } from '../core/read.ts';

export interface ScheduleBlock {
  readonly key: string;
  readonly original: unknown;
  readonly block: string;
  readonly blueprint: string;
  readonly after: string;
  readonly until: string;
  readonly count: string;
}
export type BlockField = 'block' | 'blueprint' | 'after' | 'until' | 'count';
export interface ScheduleDraft {
  readonly original: unknown;
  readonly days: Readonly<Record<string, readonly ScheduleBlock[]>>;
  readonly week: Readonly<Record<string, string>>;
}
export const WEEK_SLOTS = [
  'default',
  'monday',
  'tuesday',
  'wednesday',
  'thursday',
  'friday',
  'saturday',
  'sunday',
] as const;

function raw(value: unknown): string {
  return value === undefined ? '' : typeof value === 'string' ? value : JSON.stringify(value);
}
export function scheduleDraft(profile: unknown): ScheduleDraft | null {
  const plan = own(profile, 'plan');
  if (plan === undefined) return null;
  return {
    original: plan,
    days: Object.fromEntries(
      entries(own(plan, 'days')).map(([day, blocks]) => [
        day,
        (list(blocks) ?? []).map((block, index) => ({
          key: `${day}:${index}`,
          original: block,
          block: raw(own(block, 'block')),
          blueprint: raw(own(block, 'blueprint')),
          after: raw(own(block, 'after')),
          until: raw(own(block, 'until')),
          count: raw(own(block, 'count')),
        })),
      ]),
    ),
    week: Object.fromEntries(entries(own(plan, 'week')).map(([slot, value]) => [slot, raw(value)])),
  };
}

/** F6-11 RL8: the day plan "Start a day plan" drafts (classic.html addPlan: one day, used every day). */
export const FIRST_DAY = 'every_day';

/**
 * F6-11 RL8: a first plan for a profile without one: one day plan used for the whole week,
 * holding one block that runs `run`. Its kind is left blank for the person to choose; the
 * server checks it against the run's kind on save.
 */
export function firstPlanDraft(run: string): ScheduleDraft {
  return {
    original: undefined,
    days: {
      [FIRST_DAY]: [
        {
          key: `${FIRST_DAY}:0`,
          original: undefined,
          block: '',
          blueprint: run,
          after: '',
          until: '',
          count: '',
        },
      ],
    },
    week: { default: FIRST_DAY },
  };
}

/** Session data is untrusted; keep raw strings without re-parsing numeric inputs. */
export function restoreScheduleDraft(value: unknown): ScheduleDraft | null {
  const days: Record<string, readonly ScheduleBlock[]> = {};
  for (const [day, rawBlocks] of entries(own(value, 'days'))) {
    const blocks = list(rawBlocks);
    if (blocks === undefined) return null;
    const restored: ScheduleBlock[] = [];
    for (const block of blocks) {
      const key = text(own(block, 'key'));
      const kind = text(own(block, 'block'));
      const blueprint = text(own(block, 'blueprint'));
      const after = text(own(block, 'after'));
      const until = text(own(block, 'until'));
      const count = text(own(block, 'count'));
      if (
        key === undefined ||
        kind === undefined ||
        blueprint === undefined ||
        after === undefined ||
        until === undefined ||
        count === undefined
      )
        return null;
      restored.push({
        key,
        block: kind,
        blueprint,
        after,
        until,
        count,
        original: own(block, 'original'),
      });
    }
    days[day] = restored;
  }
  if (
    value === undefined ||
    own(value, 'days') === undefined ||
    entries(own(value, 'week')).some(([, member]) => typeof member !== 'string')
  )
    return null;
  return {
    original: own(value, 'original'),
    days,
    week: Object.fromEntries(
      entries(own(value, 'week')).map(([slot, member]) => [slot, text(member) ?? '']),
    ),
  };
}

/** Only syntax conversion: validation, limits and scheduling stay on the server. */
function countValue(value: string): string | number {
  if (!/^-?(?:0|[1-9]\d*)(?:\.0+)?$/.test(value)) return value;
  const number = Number(value);
  return Number.isSafeInteger(number) ? number : value;
}
export function schedulePlan(draft: ScheduleDraft): unknown {
  return {
    ...Object.fromEntries(entries(draft.original)),
    days: Object.fromEntries(
      Object.entries(draft.days).map(([day, blocks]) => [
        day,
        blocks.map((block) => {
          const value: Record<string, unknown> = {
            ...Object.fromEntries(entries(block.original)),
            block: block.block,
            blueprint: block.blueprint,
          };
          for (const field of ['after', 'until', 'count'] as const) {
            if (block[field] === '') Reflect.deleteProperty(value, field);
            else value[field] = field === 'count' ? countValue(block[field]) : block[field];
          }
          return value;
        }),
      ]),
    ),
    week: Object.fromEntries(Object.entries(draft.week).filter(([, value]) => value !== '')),
  };
}
export function profileRevision(body: unknown): string | null {
  return text(own(body, 'revision')) ?? null;
}
export function planOf(body: unknown): unknown {
  return ownPath(body, 'profile', 'plan');
}
