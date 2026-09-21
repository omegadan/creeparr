import type { Settings } from "../../api/types";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { num, useSettingsForm } from "./useSettingsForm";
import { ProviderAccount } from "./ProviderAccount";

export function PatreonSettings({ settings }: { settings: Settings }) {
  const f = useSettingsForm(settings, "patreon");
  return (
    <div className="grid gap-5">
      <ProviderAccount name="patreon" settings={settings} />
      <Section title="Patreon requests" description="How Patrearr talks to patreon.com. Lower the rate or add a random delay if requests get blocked.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="HTTP backend" hint="curl_cffi impersonates Chrome's TLS fingerprint (helps with Cloudflare).">
            <select className="input" value={f.form.http_backend} onChange={(e) => f.set("http_backend", e.target.value as "httpx" | "curl_cffi")}>
              <option value="httpx">httpx (default)</option>
              <option value="curl_cffi">curl_cffi (Chrome impersonation)</option>
            </select>
          </Field>
          <Field label="Requests per second" hint="Steady rate limit for API calls (0.1–10).">
            <input type="number" step="0.1" min="0.1" max="10" className="input" value={f.form.requests_per_second} onChange={(e) => f.set("requests_per_second", num(e.target.value, 1))} />
          </Field>
          <Field label="Random delay min (s)" hint="Extra random pause added before each request.">
            <input type="number" step="0.1" min="0" className="input" value={f.form.random_delay_min} onChange={(e) => f.set("random_delay_min", num(e.target.value, 0))} />
          </Field>
          <Field label="Random delay max (s)" hint="0 in both disables the random delay.">
            <input type="number" step="0.1" min="0" className="input" value={f.form.random_delay_max} onChange={(e) => f.set("random_delay_max", num(e.target.value, 0))} />
          </Field>
          <Field label="Downloads per hour" hint="Cap how many downloads start per hour. 0 = unlimited.">
            <input type="number" min="0" className="input" value={f.form.downloads_per_hour} onChange={(e) => f.set("downloads_per_hour", num(e.target.value, 0))} />
          </Field>
          <Field label="User-Agent" hint="The Patreon mobile app UA is what works best." className="sm:col-span-2">
            <input className="input font-mono text-xs" value={f.form.user_agent} onChange={(e) => f.set("user_agent", e.target.value)} />
          </Field>
        </div>
        <div className="flex justify-end"><Button variant="primary" disabled={!f.dirty} loading={f.saving} onClick={f.save}>Save</Button></div>
      </Section>
    </div>
  );
}
