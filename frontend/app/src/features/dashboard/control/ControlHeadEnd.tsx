import { lastRead } from '../../../copy/dashboard/shell.ts';
import { selectLastReadAge } from '../../../store/selectors/controlHead.ts';
import { RegionEnd } from '../../../ui/Region.tsx';
import { useView } from '../context.ts';

/** The Control card's header end: "last read {age}" of the status read (F-112: only the age text changes). */
export function ControlHeadEnd() {
  const age = useView(selectLastReadAge);
  if (age === null) return null;
  return (
    <RegionEnd>
      <time className="tp-age">{lastRead(age)}</time>
    </RegionEnd>
  );
}
