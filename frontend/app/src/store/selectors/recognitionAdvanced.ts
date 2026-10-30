import { fromServer } from '../../copy/server.ts';
import {
  analysisReceipt,
  analysisStatus,
  cropCheck,
  cropReceipt,
  cropTargetExists,
  draftRectangle,
  imagePath,
  moduleCatalogue,
  requiredImages,
  requiredRepairTarget,
  scanPlan,
  templateInventory,
} from '../../core/recognitionImages.ts';
import {
  advancedPending,
  currentAdvanced,
  type RecognitionAdvancedState,
} from '../recognitionAdvanced.ts';

// Only actual textual server fields cross this boundary. Projected object keys never do.
const server = (value: string | null) => fromServer('detail', value);
const strings = (values: readonly string[]) => values.map(server);
export function selectAdvanced(state: RecognitionAdvancedState, now: number) {
  const target = currentAdvanced(state);
  const plan = scanPlan(target?.plan);
  const required = requiredImages(target?.required);
  const inventory = templateInventory(target?.templates);
  const analysis = analysisStatus(
    target?.analysis,
    target?.selectedOperation ?? null,
    target?.analysisError !== true,
  );
  const launch = analysisReceipt(target?.launch ?? null);
  const crop = cropReceipt(target?.crop ?? null);
  return {
    // Explicit presentation state, with no raw answer or intent record exposed to components.
    target:
      target === null
        ? null
        : {
            draft: target.draft,
            frame: target.frame,
            frameError: target.frameError,
            frameBusy: target.frameBusy,
            readError: target.readError,
            readAt: target.readAt,
            imageRevision: target.imageRevision,
            busy: target.busy,
            selectedOperation: target.selectedOperation,
            analysisAt: target.analysisAt,
            analysisError: target.analysisError,
            analysisBusy: target.analysisBusy,
            snapshotError: target.snapshotError,
            checking: target.checking,
            contextError: target.contextError,
          },
    plan: {
      note: server(plan.note),
      native: plan.native,
      mapping: plan.mapping.map((step) => ({
        id: step.id,
        source: server(step.source),
        destination: server(step.destination),
        status: server(step.status),
        action: server(step.action),
      })),
      interface: plan.interface.map((screen) => ({
        id: screen.id,
        label: server(screen.label),
        status: server(screen.status),
        total: screen.total,
        verified: screen.verified,
        controls: screen.controls.map((control) => ({
          ...control,
          label: server(control.label),
          status: server(control.status),
          template: server(control.template),
        })),
      })),
      steps: plan.steps.map((step) => ({
        id: step.id,
        title: server(step.title),
        instructions: server(step.instructions),
        mode: server(step.mode),
        targets: step.targets.map((repair) => ({
          repair,
          key: repair.rel,
          label: server(repair.label),
          rel: server(repair.rel),
          status: server(repair.status),
          required: repair.required,
          automatic: repair.automatic,
          reference: repair.reference,
          learned: repair.learned,
          learnedSize: repair.learnedSize,
        })),
      })),
    },
    required: required.rows.map((row) => ({
      key: row.rel,
      rel: server(row.rel),
      feature: server(row.feature),
      note: server(row.note),
      usedBy: strings(row.usedBy),
      have: row.have,
      repair: requiredRepairTarget(row),
    })),
    missingSeen: strings(inventory.missingSeen),
    library: inventory.groups.map((group) => ({
      key: `${state.key}:${state.epoch}:${group.folder}`,
      folder: server(group.folder),
      images: group.images.map((rel) => {
        const path = imagePath(rel);
        return {
          key: rel,
          name: server(rel),
          src:
            path === null
              ? null
              : `${path}?v=${encodeURIComponent(`${state.key}:${state.epoch}:${target?.imageRevision ?? 0}:${target?.readAt ?? 0}`)}`,
        };
      }),
    })),
    catalogue: moduleCatalogue(target?.catalogue).map((module) => ({
      key: module.slug,
      name: server(module.name),
      slug: server(module.slug),
      abbrevs: strings(module.abbrevs),
      learned: module.learned,
    })),
    analysis: {
      ...analysis,
      status: server(analysis.status),
      message: server(analysis.message),
      code: server(analysis.code),
      screen: server(analysis.screen),
      candidates: strings(analysis.candidates),
      matches: analysis.matches.map((match) => ({
        ...match,
        names: strings(match.names),
        roles: strings(match.roles),
      })),
    },
    launch: {
      ...launch,
      code: server(launch.code),
      error: server(launch.error),
      message: server(launch.message),
    },
    crop: {
      ...crop,
      code: server(crop.code),
      error: server(crop.error),
      message: server(crop.message),
      rel: server(crop.rel),
    },
    exists: cropTargetExists(target?.draft.name ?? '', inventory.have, target?.crop ?? null),
    overlay: target === null ? null : draftRectangle(target.draft),
    cropCheck: cropCheck(
      target?.draft ?? { name: '', x: '', y: '', w: '', h: '' },
      target?.frame?.size ?? null,
    ),
    launchPending: advancedPending(target?.launch ?? null),
    cropPending: advancedPending(target?.crop ?? null),
    stale: target?.readAt != null && now - target.readAt > 30000,
    analysisStale: target?.analysisAt != null && now - target.analysisAt > 30000,
  };
}
export type AdvancedView = ReturnType<typeof selectAdvanced>;
