import type { Settings } from "../../api/types";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { Toggle } from "../ui/Toggle";
import { num, useSettingsForm } from "./useSettingsForm";

export function OnlyFansSettings({ settings }: { settings: Settings }) {
  const f = useSettingsForm(settings, "onlyfans");
  return (
    <div className="grid gap-5">
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
          <Field label="Dynamic rules URL" hint="Community-maintained signing rules; refreshed hourly." className="sm:col-span-2">
            <input className="input font-mono text-xs" value={f.form.dynamic_rules_url} onChange={(e) => f.set("dynamic_rules_url", e.target.value)} />
          </Field>
          <Field label="User-Agent" hint="Must match the browser you copied the cookies from." className="sm:col-span-2">
            <input className="input font-mono text-xs" placeholder="(default Chrome UA)" value={f.form.user_agent} onChange={(e) => f.set("user_agent", e.target.value)} />
          </Field>
        </div>
        <Toggle checked={f.form.include_archived} onChange={(v) => f.set("include_archived", v)} label="Include archived posts" />
        <div className="flex justify-end"><Button variant="primary" disabled={!f.dirty} loading={f.saving} onClick={f.save}>Save</Button></div>
      </Section>
    </div>
  );
}
