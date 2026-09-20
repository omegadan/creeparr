import type { Settings } from "../../api/types";
import { useTestNotifications } from "../../api/hooks/useSettings";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { Toggle } from "../ui/Toggle";
import { useToast } from "../ui/Toast";
import { useSettingsForm } from "./useSettingsForm";

export function NotificationSettings({ settings }: { settings: Settings }) {
  const f = useSettingsForm(settings, "notifications");
  const test = useTestNotifications();
  const { toast, error } = useToast();
  return (
    <div className="grid gap-5">
      <Section title="Targets" description="Where alerts are sent. Leave a field blank to disable that target.">
        <Field label="Discord webhook URL" hint="Server Settings → Integrations → Webhooks → Copy URL.">
          <input className="input font-mono text-xs" placeholder="https://discord.com/api/webhooks/…" value={f.form.discord_webhook} onChange={(e) => f.set("discord_webhook", e.target.value)} />
        </Field>
        <Field label="ntfy topic URL" hint="e.g. https://ntfy.sh/your-topic">
          <input className="input font-mono text-xs" placeholder="https://ntfy.sh/…" value={f.form.ntfy_url} onChange={(e) => f.set("ntfy_url", e.target.value)} />
        </Field>
        <Field label="Generic webhook URL" hint="Receives a JSON POST: {app,title,message,level}.">
          <input className="input font-mono text-xs" placeholder="https://…" value={f.form.webhook_url} onChange={(e) => f.set("webhook_url", e.target.value)} />
        </Field>
      </Section>
      <Section title="When to notify">
        <div className="grid gap-3 sm:grid-cols-2">
          <Toggle checked={f.form.notify_auth_invalid} onChange={(v) => f.set("notify_auth_invalid", v)} label="Session expired / invalid" hint="Most useful: tells you to re-paste cookies." />
          <Toggle checked={f.form.notify_scan_failed} onChange={(v) => f.set("notify_scan_failed", v)} label="Scan failed" />
          <Toggle checked={f.form.notify_download_failed} onChange={(v) => f.set("notify_download_failed", v)} label="Download failed / unsupported" hint="Can be noisy on large libraries." />
          <Toggle checked={f.form.notify_scan_completed} onChange={(v) => f.set("notify_scan_completed", v)} label="Scan completed" />
        </div>
        <div className="flex justify-end gap-2">
          <Button onClick={() => test.mutate(undefined, { onSuccess: (r) => (r.ok ? toast(`Sent to ${r.targets?.join(", ")}`, "success") : toast(r.detail ?? "no targets", "error")), onError: (e) => error(e) })} loading={test.isPending}>Send test</Button>
          <Button variant="primary" disabled={!f.dirty} loading={f.saving} onClick={f.save}>Save</Button>
        </div>
      </Section>
    </div>
  );
}
