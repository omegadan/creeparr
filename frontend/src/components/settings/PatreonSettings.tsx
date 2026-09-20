import { useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";
import type { Settings } from "../../api/types";
import { useClearPatreonAuth, useSetPatreonAuth, useTestPatreonAuth } from "../../api/hooks/useSettings";
import { useSystemStatus } from "../../api/hooks/useSystem";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { useToast } from "../ui/Toast";
import { num, useSettingsForm } from "./useSettingsForm";
import { formatDate } from "../../lib/format";

export function PatreonSettings({ settings }: { settings: Settings }) {
  const [sessionId, setSessionId] = useState("");
  const [cookies, setCookies] = useState("");
  const test = useTestPatreonAuth();
  const setAuth = useSetPatreonAuth();
  const clear = useClearPatreonAuth();
  const status = useSystemStatus();
  const { toast, error } = useToast();
  const f = useSettingsForm(settings, "patreon");
  const auth = status.data?.auth;

  const body = () => ({ ...(sessionId.trim() ? { session_id: sessionId.trim() } : {}), ...(cookies.trim() ? { cookies_txt: cookies } : {}) });
  const hasInput = Boolean(sessionId.trim() || cookies.trim());

  return (
    <div className="grid gap-5">
      <Section title="Patreon session" description="Patreonarr uses your own logged-in browser session to read the posts you already have access to. Nothing is bypassed; locked posts stay locked.">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-fg-muted">Current status:</span>
          {auth ? <Badge tone={auth.state === "valid" ? "ok" : auth.state === "unknown" ? "warn" : "danger"}>{auth.state}</Badge> : "…"}
          {auth?.user_name && <span>signed in as <b>{auth.user_name}</b></span>}
          {auth?.error && <span className="text-danger">{auth.error}</span>}
          {auth?.checked_at && <span className="text-fg-dim">checked {formatDate(auth.checked_at, true)}</span>}
          <span className="text-fg-dim">· stored session: {settings.patreon.session_id || "none"}{settings.patreon.has_cookies_txt ? " + cookies.txt" : ""}</span>
        </div>
        <Field label="session_id cookie" hint="Log in to patreon.com in your browser, open DevTools → Application → Cookies → patreon.com, and copy the value of session_id.">
          <input className="input font-mono" placeholder="paste the session_id value" value={sessionId} onChange={(e) => setSessionId(e.target.value)} autoComplete="off" spellCheck={false} />
        </Field>
        <Field label="cookies.txt (optional)" hint="A full Netscape-format export (e.g. from the 'Get cookies.txt' extension). Extra cookies such as cf_clearance are forwarded, which helps if Cloudflare blocks requests.">
          <textarea className="input h-28 font-mono text-xs" placeholder="# Netscape HTTP Cookie File…" value={cookies} onChange={(e) => setCookies(e.target.value)} spellCheck={false} />
        </Field>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            onClick={() => test.mutate(hasInput ? body() : undefined, { onSuccess: (r) => (r.ok ? toast(`Connected as ${r.user?.full_name ?? r.user?.id}`, "success") : toast(`${r.reason}: ${r.detail ?? ""}`, "error")), onError: (e) => error(e) })}
            loading={test.isPending}
          >
            Test {hasInput ? "pasted values" : "stored session"}
          </Button>
          <Button variant="primary" disabled={!hasInput} loading={setAuth.isPending} onClick={() => setAuth.mutate(body(), { onSuccess: (r) => { if (r.ok) { toast(`Saved and connected as ${r.user?.full_name ?? ""}`, "success"); setSessionId(""); setCookies(""); } else toast(`Saved, but the test failed: ${r.reason}: ${r.detail ?? ""}`, "error"); }, onError: (e) => error(e) })}>
            Save &amp; connect
          </Button>
          <Button variant="ghost" onClick={() => clear.mutate(undefined, { onSuccess: () => toast("Session cleared"), onError: (e) => error(e) })}>Clear stored session</Button>
          {test.data && (test.data.ok ? <CheckCircle2 className="h-4 w-4 text-ok" /> : <XCircle className="h-4 w-4 text-danger" />)}
        </div>
      </Section>

      <Section title="HTTP behaviour" description="How Patreonarr talks to patreon.com. Change these only if requests are being blocked.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="HTTP backend" hint={status.data?.curl_cffi_available ? "curl_cffi impersonates Chrome's TLS fingerprint (helps with Cloudflare)." : "curl_cffi is not installed in this image; httpx will be used."}>
            <select className="input" value={f.form.http_backend} onChange={(e) => f.set("http_backend", e.target.value as "httpx" | "curl_cffi")}>
              <option value="httpx">httpx (default)</option>
              <option value="curl_cffi">curl_cffi (Chrome impersonation)</option>
            </select>
          </Field>
          <Field label="Requests per second" hint="Global rate limit for API calls. Keep it low to be polite.">
            <input type="number" step="0.1" min="0.1" max="10" className="input" value={f.form.requests_per_second} onChange={(e) => f.set("requests_per_second", num(e.target.value, 1))} />
          </Field>
          <Field label="User-Agent" hint="The Patreon mobile app UA is what other tools use successfully." className="sm:col-span-2">
            <input className="input font-mono text-xs" value={f.form.user_agent} onChange={(e) => f.set("user_agent", e.target.value)} />
          </Field>
        </div>
        <div className="flex justify-end"><Button variant="primary" disabled={!f.dirty} loading={f.saving} onClick={f.save}>Save</Button></div>
      </Section>
    </div>
  );
}
