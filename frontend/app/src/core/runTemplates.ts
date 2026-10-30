import { entries, id, list, own, ownPath, record } from './read.ts';

/**
 * Shipped gathering defaults from GET /api/run-templates (Collection C5).
 * Runtime collection evidence belongs to collection.ts; this reader owns only
 * the template catalogue. Values are retained verbatim, never filled or relabeled.
 */
export interface GatherDefault {
  readonly templateId: string;
  readonly templateLabel: string;
  readonly ref: string;
  readonly value: unknown;
}

export function gatherDefaults(body: unknown): readonly GatherDefault[] {
  const templates = list(own(body, 'templates'));
  if (templates === undefined || own(body, 'ok') === false) return [];
  return templates.flatMap((template): GatherDefault[] => {
    const templateId = id(own(template, 'id'));
    const templateLabel = id(own(template, 'label'));
    if (templateId === undefined || templateLabel === undefined) return [];
    return entries(ownPath(template, 'policies', 'gather')).flatMap(([ref, value]) =>
      ref === '' || record(value) === undefined || entries(value).length === 0
        ? []
        : [{ templateId, templateLabel, ref, value }],
    );
  });
}
