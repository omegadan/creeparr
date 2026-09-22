import type { Settings } from "../../api/types";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { num, useSettingsForm } from "./useSettingsForm";
import { ProviderAccount } from "./ProviderAccount";

export function RedditSettings({ settings }: { settings: Settings }) {
  const f = useSettingsForm(settings, "reddit");
  return (
    <div className="grid gap-5">
      <ProviderAccount name="reddit" settings={settings} />
      <Section title="Reddit" description="Add a subreddit or user in Add creator: r/name, u/name, or a full URL. Public content works without login on most home connections; if Reddit blocks requests, create a free Reddit app below.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Max posts per source" hint="0 = as many as Reddit lists (~1000 cap).">
            <input type="number" min="0" className="input" value={f.form.max_posts} onChange={(e) => f.set("max_posts", num(e.target.value, 0))} />
          </Field>
          <Field label="Downloads per hour" hint="Cap how many downloads start per hour; spaced out at random (Downloads → Pacing). 0 = unlimited.">
            <input type="number" min="0" className="input" value={f.form.downloads_per_hour} onChange={(e) => f.set("downloads_per_hour", num(e.target.value, 0))} />
          </Field>
        </div>
        <Field label="User-Agent" hint="Reddit requires a descriptive User-Agent for the API.">
          <input className="input font-mono text-xs" placeholder="(default browser UA)" value={f.form.user_agent} onChange={(e) => f.set("user_agent", e.target.value)} />
        </Field>
        <div className="flex justify-end"><Button variant="primary" disabled={!f.dirty} loading={f.saving} onClick={f.save}>Save</Button></div>
      </Section>
    </div>
  );
}
