import type { Plugin } from 'vite';
import { copy } from '../src/copy/catalogue.ts';

/**
 * index.html is the one page shell outside React. Its title and noscript
 * wording come from the copy catalogue too: each placeholder is replaced
 * with escaped catalogue text, in development and in the build.
 */
export const HTML_COPY: Readonly<Record<string, string>> = {
  __TP_APP_TITLE__: copy.app.title.short,
  __TP_NO_SCRIPT__: copy.app.noScript.short,
};

export function escapeHtml(text: string): string {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

/** Fill the placeholders; fail when one is missing so the shell and catalogue stay in step. */
export function fillHtmlCopy(html: string): string {
  let filled = html;
  for (const [placeholder, text] of Object.entries(HTML_COPY)) {
    if (!filled.includes(placeholder)) {
      throw new Error(`index.html has no ${placeholder} placeholder`);
    }
    filled = filled.replaceAll(placeholder, escapeHtml(text));
  }
  return filled;
}

export function htmlCopy(): Plugin {
  return { name: 'tower-pilot-html-copy', transformIndexHtml: fillHtmlCopy };
}
