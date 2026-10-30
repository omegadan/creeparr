/** What the UI derives from the providers' descriptions (see /providers). */

interface Described {
  name: string;
  label: string;
  post_types: Record<string, string>;
}

/** Every provider's post types with their labels. */
export function postTypeLabels(providers: readonly Described[]): Record<string, string> {
  return Object.assign({}, ...providers.map((p) => p.post_types));
}

export function postTypeLabel(labels: Record<string, string>, t: string | null): string {
  if (!t) return "–";
  return labels[t] ?? t.replace(/_/g, " ");
}

export function providerLabel(providers: readonly Described[], name: string): string {
  return providers.find((p) => p.name === name)?.label ?? name;
}

/** A provider's hand-made settings form, or the generic one when it has none. */
export function settingsForm<F>(name: string, forms: Readonly<Record<string, F>>, generic: F): F {
  return Object.hasOwn(forms, name) ? forms[name] : generic;
}
