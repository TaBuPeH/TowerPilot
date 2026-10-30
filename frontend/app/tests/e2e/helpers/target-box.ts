import type { Locator } from '@playwright/test';

export interface TargetMinimum {
  readonly width: number;
  readonly height: number;
  readonly buttonHeight?: number;
}

/** Measure clickable wrapping labels for checkboxes/radios, and controls for other fields. */
export async function undersizedControlTargets(root: Locator, minimum: TargetMinimum) {
  return root.evaluate(
    (element, minimum) =>
      [...element.querySelectorAll('button, select, input, summary')].flatMap((control) => {
        const controlBox = control.getBoundingClientRect();
        if (
          controlBox.width === 0 ||
          controlBox.height === 0 ||
          !control.checkVisibility({ visibilityProperty: true, opacityProperty: true })
        )
          return [];
        const label =
          control instanceof HTMLInputElement &&
          (control.type === 'checkbox' || control.type === 'radio')
            ? [...(control.labels ?? [])].find((candidate) => {
                const box = candidate.getBoundingClientRect();
                return (
                  candidate.contains(control) &&
                  box.width > 0 &&
                  box.height > 0 &&
                  getComputedStyle(candidate).visibility === 'visible'
                );
              })
            : undefined;
        const target = label ?? control;
        const box = target.getBoundingClientRect();
        const height =
          control.tagName === 'BUTTON' ? (minimum.buttonHeight ?? minimum.height) : minimum.height;
        return box.width < minimum.width || box.height < height
          ? [{ id: control.id, html: control.outerHTML, width: box.width, height: box.height }]
          : [];
      }),
    minimum,
  );
}
