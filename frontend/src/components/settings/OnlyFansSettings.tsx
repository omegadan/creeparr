import { AlertTriangle } from "lucide-react";
import type { Settings } from "../../api/types";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { Toggle } from "../ui/Toggle";
import { num, useSettingsForm } from "./useSettingsForm";
import { ProviderAccount } from "./ProviderAccount";

export function OnlyFansSettings({ settings }: { settings: Settings }) {
  const f = useSettingsForm(settings, "onlyfans");
  return (
    <div className="grid gap-5">
      <div className="flex items-start gap-3 rounded-lg border-2 border-danger/50 bg-danger/10 p-4 text-sm text-danger">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
        <div>
          <div className="font-bold">Automated downloading violates OnlyFans' Terms of Service.</div>
          Using this can get your account suspended or banned. It is intended only for personal
          archival of content you pay for, on an account you accept the risk of losing. Keep the
          request rate low and do not redistribute anything you download.
        </div>
      </div>
      <ProviderAccount name="onlyfans" settings={settings} />
      <Section title="OnlyFans behaviour" description="Signing rules are fetched from the URL below and cached for an hour. Change these only if requests are being blocked.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="HTTP backend" hint="curl_cffi impersonates Chrome's TLS fingerprint.">
            <select className="input" value={f.form.http_backend} onChange={(e) => f.set("http_backend", e.target.value as "httpx" | "curl_cffi")}>
              <option value="httpx">httpx (default)</option>
              <option value="curl_cffi">curl_cffi (Chrome impersonation)</option>
            </select>
          </Field>
          <Field label="Requests per second" hint="Keep this low; OnlyFans flags aggressive automation.">
            <input type="number" step="0.1" min="0.1" max="5" className="input" value={f.form.requests_per_second} onChange={(e) => f.set("requests_per_second", num(e.target.value, 0.5))} />
          </Field>
          <Field label="Random delay min (s)" hint="Extra random pause before each request.">
            <input type="number" step="0.1" min="0" className="input" value={f.form.random_delay_min} onChange={(e) => f.set("random_delay_min", num(e.target.value, 0))} />
          </Field>
          <Field label="Random delay max (s)" hint="0 in both disables it. Helps avoid detection.">
            <input type="number" step="0.1" min="0" className="input" value={f.form.random_delay_max} onChange={(e) => f.set("random_delay_max", num(e.target.value, 0))} />
          </Field>
          <Field label="Downloads per hour" hint="Cap how many downloads start per hour; spaced out at random (Downloads → Pacing). 0 = unlimited.">
            <input type="number" min="0" className="input" value={f.form.downloads_per_hour} onChange={(e) => f.set("downloads_per_hour", num(e.target.value, 0))} />
          </Field>
          <Field label="Dynamic rules URL" hint="Community-maintained signing rules; refreshed hourly." className="sm:col-span-2">
            <input className="input font-mono text-xs" value={f.form.dynamic_rules_url} onChange={(e) => f.set("dynamic_rules_url", e.target.value)} />
          </Field>
          <Field label="User-Agent" hint="Must match the browser you copied the cookies from." className="sm:col-span-2">
            <input className="input font-mono text-xs" placeholder="(default Chrome UA)" value={f.form.user_agent} onChange={(e) => f.set("user_agent", e.target.value)} />
          </Field>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Toggle checked={f.form.include_archived} onChange={(v) => f.set("include_archived", v)} label="Include archived posts" />
          <Toggle checked={f.form.include_messages} onChange={(v) => f.set("include_messages", v)} label="Include messages / paid DMs" hint="Archives media from your chats with the creator." />
        </div>
        <div className="flex justify-end"><Button variant="primary" disabled={!f.dirty} loading={f.saving} onClick={f.save}>Save</Button></div>
      </Section>
    </div>
  );
}
