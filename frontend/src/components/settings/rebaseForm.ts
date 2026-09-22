const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/**
 * Move a form onto fresh server values without losing unsaved edits.
 *
 * Fields the user changed (form differs from the server copy the form started from)
 * keep the user's value; every other field takes the new server value. So saving a
 * different section, flipping a toggle that saves on its own, or a background refetch
 * no longer wipes what someone is typing.
 */
export function rebaseForm<T extends object>(form: T, oldServer: T, newServer: T): T {
  const out = { ...newServer };
  for (const key of Object.keys(form) as (keyof T)[]) {
    if (!same(form[key], oldServer[key])) out[key] = form[key];
  }
  return out;
}
