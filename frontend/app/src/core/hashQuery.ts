/** Decode a hash query without browser APIs; malformed escaping stays distinguishable. */
export function queryFromHash(hash: string): Readonly<Record<string, string>> {
  const start = hash.indexOf('?');
  if (start === -1) return {};
  const decode = (value: string) => decodeURIComponent(value.replace(/\+/g, ' '));
  try {
    return Object.fromEntries(
      hash
        .slice(start + 1)
        .split('&')
        .filter((part) => part !== '')
        .map((part) => {
          const equals = part.indexOf('=');
          return equals === -1
            ? [decode(part), '']
            : [decode(part.slice(0, equals)), decode(part.slice(equals + 1))];
        }),
    );
  } catch {
    return { __invalid_hash__: '' };
  }
}
