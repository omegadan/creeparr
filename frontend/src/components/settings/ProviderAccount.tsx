import { useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";
import type { CredentialField, Settings } from "../../api/types";
import { useProviders } from "../../api/hooks/useCreators";
import { useClearAuth, useSetAuth, useTestAuth, useUpdateSettings } from "../../api/hooks/useSettings";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { Toggle } from "../ui/Toggle";
import { useToast } from "../ui/Toast";
import { formatDate } from "../../lib/format";

export function ProviderAccount({ name, settings }: { name: string; settings: Settings }) {
  const providers = useProviders();
  const provider = (providers.data ?? []).find((p) => p.name === name);
  const [form, setForm] = useState<Record<string, string>>({});
  const test = useTestAuth(name);
  const setAuth = useSetAuth(name);
  const clear = useClearAuth(name);
  const { toast, error } = useToast();
  const update = useUpdateSettings();

  if (!provider) return null;

  const auth = provider.auth;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const providerEnabled = ((settings as any)[name]?.enabled ?? true) as boolean;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const stored = (settings as any)[name] ?? {};

  const filled = Object.fromEntries(Object.entries(form).filter(([, v]) => v.trim() !== ""));
  const hasInput = Object.keys(filled).length > 0;
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const isStored = (field: CredentialField) =>
    field.multiline ? !!stored[`has_${field.key}`] : !!(stored[field.key] && stored[field.key] !== "none");
  const hasStored = provider.credentials.some(isStored);

  return (
    <Section title="Account" description={provider.intro}>
      <div className="mb-1">
        <Toggle checked={providerEnabled} onChange={(v) => update.mutate({ [name]: { enabled: v } }, { onSuccess: () => toast(v ? `${provider.label} enabled` : `${provider.label} disabled`), onError: (e) => error(e) })} label={`${provider.label} enabled`} hint="When off, this provider is skipped entirely (no scans or downloads)." />
      </div>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="text-fg-muted">Status:</span>
        <Badge tone={auth.state === "valid" ? "ok" : auth.state === "unknown" ? "warn" : "danger"}>{auth.state}</Badge>
        {auth.user_name && <span>as <b>{auth.user_name}</b></span>}
        {auth.error && <span className="text-danger">{auth.error}</span>}
        {auth.checked_at && <span className="text-fg-dim">checked {formatDate(auth.checked_at, true)}</span>}
      </div>
      {provider.credentials.map((spec) => {
        const key = spec.key;
        const storedVal = stored[key] || (spec.multiline ? (stored[`has_${key}`] ? "(set)" : "none") : "none");
        return (
          <Field key={key} label={spec.label} hint={spec.hint || undefined}>
            {spec.multiline ? (
              <textarea className="input h-24 font-mono text-xs" placeholder="# Netscape HTTP Cookie File…" value={form[key] ?? ""} onChange={(e) => set(key, e.target.value)} spellCheck={false} />
            ) : (
              <input className="input font-mono" autoComplete="off" spellCheck={false} placeholder={`stored: ${storedVal}`} value={form[key] ?? ""} onChange={(e) => set(key, e.target.value)} />
            )}
            {isStored(spec) && (
              <span className="mt-1 flex items-center gap-1 text-xs text-ok">
                <CheckCircle2 className="h-3.5 w-3.5" /> Stored. Paste a new value to replace it, or Clear below to remove it.
              </span>
            )}
          </Field>
        );
      })}
      <div className="flex flex-wrap items-center gap-2">
        <Button loading={test.isPending} onClick={() => test.mutate(hasInput ? filled : undefined, { onSuccess: (r) => (r.ok ? toast(`Connected as ${r.user?.full_name ?? r.user?.id}`, "success") : toast(`${r.reason}: ${r.detail ?? ""}`, "error")), onError: (e) => error(e) })}>
          Test {hasInput ? "entered values" : "stored session"}
        </Button>
        <Button variant="primary" disabled={!hasInput} loading={setAuth.isPending} onClick={() => setAuth.mutate(filled, { onSuccess: (r) => { if (r.ok) { toast(`Saved and connected as ${r.user?.full_name ?? ""}`, "success"); setForm({}); } else toast(`Saved, but the test failed: ${r.reason}: ${r.detail ?? ""}`, "error"); }, onError: (e) => error(e) })}>
          Save &amp; connect
        </Button>
        <Button variant="ghost" disabled={!hasStored} loading={clear.isPending} onClick={() => clear.mutate(undefined, { onSuccess: () => { toast("Stored credentials cleared"); setForm({}); }, onError: (e) => error(e) })}>Clear</Button>
        {test.data && (test.data.ok ? <CheckCircle2 className="h-4 w-4 text-ok" /> : <XCircle className="h-4 w-4 text-danger" />)}
      </div>
    </Section>
  );
}
