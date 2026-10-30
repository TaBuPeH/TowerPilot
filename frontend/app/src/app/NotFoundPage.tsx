import { copy } from '../copy/catalogue.ts';
import { Why } from '../ui/Why.tsx';
import { hrefFor } from './router.ts';

export function NotFoundPage() {
  return (
    <section aria-labelledby="not-found-title">
      <h1 id="not-found-title" className="tp-page-title">
        {copy.routes.notFound.short}
      </h1>
      <Why why={copy.routes.notFound.why} />
      <p>
        <a href={hrefFor('dashboard')}>{copy.routes.backToDashboard.short}</a>
      </p>
    </section>
  );
}
