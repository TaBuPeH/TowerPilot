// @vitest-environment node
import { APP } from '../../support/paths.ts';
import path from 'node:path';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { escapeHtml, fillHtmlCopy } from '../../../scripts/html-copy.ts';
import { copy } from '../../../src/copy/catalogue.ts';

const INDEX_HTML = readFileSync(path.join(APP, 'index.html'), 'utf8');

describe('index.html copy', () => {
  it('fills the title and noscript wording from the catalogue', () => {
    const html = fillHtmlCopy(INDEX_HTML);
    expect(html).toContain(`<title>${copy.app.title.short}</title>`);
    expect(html).toContain(`<noscript>${copy.app.noScript.short}</noscript>`);
    expect(html).not.toMatch(/__TP_[A-Z_]+__/);
  });

  it('refuses a shell that lost a placeholder', () => {
    expect(() => fillHtmlCopy('<title>Fixed</title>')).toThrow(/__TP_APP_TITLE__/);
  });

  it('escapes markup characters', () => {
    expect(escapeHtml('<b>"A & B"</b>')).toBe('&lt;b&gt;&quot;A &amp; B&quot;&lt;/b&gt;');
  });
});
