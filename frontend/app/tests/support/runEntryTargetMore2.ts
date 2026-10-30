import { dashboardPagesCase } from './dashboardFixture.ts';

export const more2Case = (name: string) => dashboardPagesCase(`profile_target_more_2.${name}`);

/** r12f records each before/after GET explicitly, in one sequential world. */
export const more2Writes = [
  ['inactive_farm_label_deleted', 'farm_label', 'deleted'],
  ['inactive_farm_gathering_policy_deleted', 'farm_gathering_policy', 'deleted'],
  ['inactive_farm_rescue_policy_deleted', 'farm_rescue_policy', 'deleted'],
  ['inactive_dissonant_tab_deleted', 'dissonant_tab', 'deleted'],
  ['inactive_quest_rides_saved', 'quest_rides', 'saved'],
  ['inactive_quest_cycles_saved', 'quest_cycles', 'saved'],
  ['inactive_quest_rides_deleted', 'quest_rides', 'deleted'],
].map(([name = '', field = '', operation = '']) => ({
  name,
  write: more2Case(name),
  before: more2Case(`read_before_the_${field}_is_${operation}`),
  after: more2Case(`read_after_the_${field}_was_${operation}`),
}));
