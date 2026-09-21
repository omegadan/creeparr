import { useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";
import type { Settings } from "../../api/types";
import { useProviders } from "../../api/hooks/useCreators";
import { useClearAuth, useSetAuth, useTestAuth, useUpdateSettings } from "../../api/hooks/useSettings";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { Toggle } from "../ui/Toggle";
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
  sessionid: { label: "sessionid cookie", hint: "instagram.com → DevTools → Application → Cookies → sessionid" },
  sess: { label: "sess cookie", hint: "onlyfans.com → DevTools → Application → Cookies → sess" },
  auth_id: { label: "auth_id cookie", hint: "onlyfans.com cookie auth_id (your numeric user id)" },
  x_bc: { label: "x-bc token", hint: "Request header x-bc from any onlyfans.com/api2 request in the Network tab" },
  cookies_txt: { label: "cookies.txt (optional)", hint: "Full Netscape cookie export; extra cookies (cf_clearance) are forwarded", multiline: true, optional: true },
  client_id: { label: "Reddit app client id", hint: "reddit.com/prefs/apps → create a \"script\" app → the id under the app name", optional: true },
  client_secret: { label: "Reddit app secret", hint: "The \"secret\" field of your Reddit script app", optional: true },
};

const INTRO: Record<string, string> = {
  patreon: "Reads posts your Patreon account already has access to. Locked posts stay locked.",
  onlyfans: "Reads content your OnlyFans account is subscribed to. Signing rules are fetched automatically. OnlyFans is strict about automation; use a low request rate and expect DRM videos to be skipped.",
  youtube: "Public YouTube channels need no login. Add a cookies.txt only for members-only or age-restricted videos.",
  instagram: "Reads Instagram from your logged-in session. Copy the sessionid cookie from instagram.com in your browser. Instagram is strict about automation; use a dedicated account.",
  reddit: "Public subreddits and users work without login. Only add a Reddit app (client id + secret from reddit.com/prefs/apps, type: script) if Reddit blocks anonymous requests on your network.",
};

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
  const isStored = (key: string) =>
    (FIELDS[key]?.multiline ? !!stored[`has_${key}`] : !!(stored[key] && stored[key] !== "none"));
  const hasStored = provider.credential_fields.some(isStored);

  return (
    <Section title="Account" description={INTRO[name]}>
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
            {isStored(key) && (
              <p className="mt-1 flex items-center gap-1 text-xs text-ok">
                <CheckCircle2 className="h-3.5 w-3.5" /> Stored. Paste a new value to replace it, or Clear below to remove it.
              </p>
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
