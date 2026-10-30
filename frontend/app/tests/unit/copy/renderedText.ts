import { copy, observeCopy } from '../../../src/copy/catalogue.ts';
import { observeServerText } from '../../../src/copy/server.ts';

/**
 * The runtime backstop of ADR-001 rule 5 at the render boundary (Amendment 1:
 * lint is a guardrail; this check, review and the type rules together are the
 * safety). Lint refuses statically every rendered value that is not a
 * CopyNode; this helper checks what a rendered tree actually shows: every
 * text node and every attribute a person reads or hears must be text the
 * catalogue made - an entry, or the output of a catalogue formatter such as
 * `elapsedSeconds(12)` - or a number. Importing this module starts listening
 * to the catalogue (`observeCopy`); tests/unit/setup.ts imports it first, so every
 * formatter call in a test run is heard.
 *
 * Form-control values are user data, not copy: a textarea's text and the
 * value of an input or select are not checked (a button-like input's value is
 * its label and is). The check compares values, not where each string came
 * from: a plain string equal to catalogue text passes (known limit R3-R4-L03;
 * lint refuses plain strings in the first place).
 *
 * Server text (dashboard-spec 6.3) has its own rule: a text node inside a
 * `data-server-text` marker (only ServerValue renders one) must be text that
 * `fromServer` made during the run, whatever catalogue text it equals - an
 * account may be called 'Tower Pilot' - and the marker holds nothing else.
 * Outside markers the catalogue rule above applies unchanged, so the value
 * comparison cannot tell server text from equal catalogue text there; server
 * text made by fromServer but rendered outside a marker is reported.
 */
/** Attributes whose text a person reads or hears; the render check reads them too. */
export const VISIBLE_ATTRIBUTES: readonly string[] = [
  'title',
  'alt',
  'placeholder',
  'label',
  'abbr',
  'aria-label',
  'aria-description',
  'aria-roledescription',
  'aria-valuetext',
  'aria-placeholder',
  'aria-braillelabel',
  'aria-brailleroledescription',
  'xlink:title',
];
function strings(node: unknown, into: Set<string>): Set<string> {
  if (typeof node === 'string') {
    into.add(node);
  } else if (typeof node === 'object' && node !== null) {
    for (const value of Object.values(node)) {
      strings(value, into);
    }
  }
  return into;
}

/** The catalogue's entries (made when it loaded), then everything its formatters make while tests run. */
const catalogue: Set<string> = strings(copy, new Set());
observeCopy((text) => {
  catalogue.add(text);
});

/** Every text a ServerText showed while tests ran (src/copy/server.ts). */
const serverShown = new Set<string>();
observeServerText((text) => {
  serverShown.add(text);
});

const BUTTON_INPUTS = new Set(['button', 'submit', 'reset']);
/** How React prints a finite number (1e+21 and 1e-7 included); NaN and Infinity are reported. */
const NUMBER = /^-?\d+(?:[.,]\d+)?(?:e[+-]?\d+)?$/i;

/** What `root` shows that is neither a catalogue string nor a number, as `where: "text"`. */
export function textOutsideCatalogue(root: Element): string[] {
  const found: string[] = [];
  const check = (where: string, text: string) => {
    const trimmed = text.trim();
    if (trimmed === '' || catalogue.has(text) || catalogue.has(trimmed) || NUMBER.test(trimmed)) {
      return;
    }
    found.push(`${where}: ${JSON.stringify(text)}`);
  };
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    // A textarea's text is its value: what the person typed, not copy (R3-R4-03).
    if (node.parentElement instanceof HTMLTextAreaElement) continue;
    const marker = node.parentElement?.closest('[data-server-text]') ?? null;
    if (marker !== null) {
      const text = node.nodeValue ?? '';
      if (marker.childElementCount === 0 && !serverShown.has(text)) {
        const tag = marker.tagName.toLowerCase();
        found.push(`server text in <${tag} data-server-text>: ${JSON.stringify(text)}`);
      }
      continue;
    }
    check(`text in <${node.parentElement?.tagName.toLowerCase() ?? '?'}>`, node.nodeValue ?? '');
  }
  for (const element of [root, ...root.querySelectorAll('*')]) {
    const tag = element.tagName.toLowerCase();
    if (element.hasAttribute('data-server-text') && element.childElementCount > 0) {
      found.push(`server text in <${tag} data-server-text>: marker holds an element`);
    }
    for (const name of VISIBLE_ATTRIBUTES) {
      const value = element.getAttribute(name);
      if (value !== null) {
        check(`<${tag} ${name}>`, value);
      }
    }
    // Form-control values are user data (input, textarea and select value and
    // defaultValue are never read here), except a button-like input's value,
    // which is its label.
    if (element instanceof HTMLInputElement && BUTTON_INPUTS.has(element.type)) {
      check(`<${tag} value>`, element.value);
    }
  }
  return found;
}
