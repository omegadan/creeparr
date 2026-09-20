import { useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";
import type { ProviderInfo, Settings } from "../../api/types";
import { useProviders } from "../../api/hooks/useCreators";
import { useClearAuth, useSetAuth, useTestAuth } from "../../api/hooks/useSettings";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { useToast } from "../ui/Toast";
import { formatDate } from "../../lib/format";

interface FieldSpec {
  label: string;
  hint?: string;
  multiline?: boolean;
  optional?: boolean;
}

const FIELDS: Record<string, FieldSpec> = {
  session_id: { label: "session_id cookie", hint: "patreon.com → DevTools → Application → Cookies → session_id" },
  sess: { label: "sess cookie", hint: "onlyfans.com → DevTools → Application → Cookies → sess" },
  auth_id: { label: "auth_id cookie", hint: "onlyfans.com cookie auth_id (your numeric user id)" },
  x_bc: { label: "x-bc token", hint: "Request header x-bc from any onlyfans.com/api2 request in the Network tab" },
  cookies_txt: { label: "cookies.txt (optional)", hint: "Full Netscape cookie export; extra cookies (cf_clearance) are forwarded", multiline: true, optional: true },
};

const INTRO: Record<string, string> = {
  patreon: "Reads posts your Patreon account already has access to. Locked posts stay locked.",
  onlyfans: "Reads content your OnlyFans account is subscribed to. Signing rules are fetched automatically. OnlyFans is strict about automation; use a low request rate and expect DRM videos to be skipped.",
  youtube: "Public YouTube channels need no login. Add a cookies.txt only for members-only or age-restricted videos.",
};

function ProviderCard({ provider, settings }: { provider: ProviderInfo; settings: Settings }) {
  const [form, setForm] = useState<Record<string, string>>({});
  const test = useTestAuth(provider.name);
  const setAuth = useSetAuth(provider.name);
  const clear = useClearAuth(provider.name);
  const { toast, error } = useToast();
  const auth = provider.auth;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const stored = (settings as any)[provider.name] ?? {};

  const filled = Object.fromEntries(Object.entries(form).filter(([, v]) => v.trim() !== ""));
  const hasInput = Object.keys(filled).length > 0;
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  return (
    <Section title={`${provider.label} account`} description={INTRO[provider.name]}>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="text-fg-muted">Status:</span>
        <Badge tone={auth.state === "valid" ? "ok" : auth.state === "unknown" ? "warn" : "danger"}>{auth.state}</Badge>
        {auth.user_name && <span>as <b>{auth.user_name}</b></span>}
        {auth.error && <span className="text-danger">{auth.error}</span>}
        {auth.checked_at && <span className="text-fg-dim">checked {formatDate(auth.checked_at, true)}</span>}
      </div>
      {provider.credential_fields.map((key) => {
        const spec = FIELDS[key] ?? { label: key };
        const storedVal = stored[key] || (spec.multiline ? (stored[`has_${key}`] ? "(set)" : "none") : "none");
        return (
          <Field key={key} label={spec.label} hint={spec.hint}>
            {spec.multiline ? (
              <textarea className="input h-24 font-mono text-xs" placeholder="# Netscape HTTP Cookie File…" value={form[key] ?? ""} onChange={(e) => set(key, e.target.value)} spellCheck={false} />
            ) : (
              <input className="input font-mono" autoComplete="off" spellCheck={false} placeholder={`stored: ${storedVal}`} value={form[key] ?? ""} onChange={(e) => set(key, e.target.value)} />
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
        <Button variant="ghost" onClick={() => clear.mutate(undefined, { onSuccess: () => toast("Session cleared"), onError: (e) => error(e) })}>Clear</Button>
        {test.data && (test.data.ok ? <CheckCircle2 className="h-4 w-4 text-ok" /> : <XCircle className="h-4 w-4 text-danger" />)}
      </div>
    </Section>
  );
}

export function AccountSettings({ settings }: { settings: Settings }) {
  const providers = useProviders();
  return (
    <div className="grid gap-5">
      {(providers.data ?? []).map((p) => <ProviderCard key={p.name} provider={p} settings={settings} />)}
    </div>
  );
}
