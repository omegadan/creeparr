import { AlertTriangle } from "lucide-react";
import type { Settings } from "../../api/types";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { Toggle } from "../ui/Toggle";
import { num, useSettingsForm } from "./useSettingsForm";

export function InstagramSettings({ settings }: { settings: Settings }) {
  const f = useSettingsForm(settings, "instagram");
  return (
    <div className="grid gap-5">
      <div className="flex items-start gap-3 rounded-lg border-2 border-danger/50 bg-danger/10 p-4 text-sm text-danger">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
        <div>
          <div className="font-bold">Instagram aggressively blocks automated access.</div>
          Scraping can flag, rate-limit, or permanently lock your account, and Instagram's private
          API changes often, so this provider will break periodically. Use a dedicated throwaway
          account, keep the request delay high, and never point it at an account you cannot afford
          to lose.
        </div>
      </div>
      <Section title="Instagram" description="Add a profile in Add creator by URL or @handle. Instagram blocks automation aggressively; keep the request delay reasonable and use a dedicated account.">
        <div className="grid gap-3 sm:grid-cols-2">
          <Toggle checked={f.form.include_reels} onChange={(v) => f.set("include_reels", v)} label="Reels" />
          <Toggle checked={f.form.include_stories} onChange={(v) => f.set("include_stories", v)} label="Stories" hint="Only stories live at scan time." />
          <Toggle checked={f.form.include_highlights} onChange={(v) => f.set("include_highlights", v)} label="Highlights" />
          <Toggle checked={f.form.include_tagged} onChange={(v) => f.set("include_tagged", v)} label="Tagged posts" />
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Max posts per profile" hint="0 = entire profile.">
            <input type="number" min="0" className="input" value={f.form.max_posts} onChange={(e) => f.set("max_posts", num(e.target.value, 0))} />
          </Field>
          <Field label="Delay between requests (s)" hint="Higher is safer against blocks.">
            <input type="number" step="0.5" min="0" className="input" value={f.form.sleep_request} onChange={(e) => f.set("sleep_request", num(e.target.value, 1))} />
          </Field>
          <Field label="Downloads per hour" hint="0 = unlimited.">
            <input type="number" min="0" className="input" value={f.form.downloads_per_hour} onChange={(e) => f.set("downloads_per_hour", num(e.target.value, 0))} />
          </Field>
        </div>
        <Field label="User-Agent" hint="Match the browser you copied the sessionid from.">
          <input className="input font-mono text-xs" placeholder="(default Chrome UA)" value={f.form.user_agent} onChange={(e) => f.set("user_agent", e.target.value)} />
        </Field>
        <div className="flex justify-end"><Button variant="primary" disabled={!f.dirty} loading={f.saving} onClick={f.save}>Save</Button></div>
      </Section>
    </div>
  );
}
