import type { Settings } from "../../api/types";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { Toggle } from "../ui/Toggle";
import { num, useSettingsForm } from "./useSettingsForm";

export function ScanSettings({ settings }: { settings: Settings }) {
  const f = useSettingsForm(settings, "scan");
  return (
    <div className="grid gap-5">
      <Section title="Scheduled scanning" description="How often monitored creators are checked for new posts.">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Scan interval (minutes)" hint="0 disables scheduled scans.">
            <input type="number" min="0" className="input" value={f.form.interval_minutes} onChange={(e) => f.set("interval_minutes", num(e.target.value, 60))} />
          </Field>
          <Field label="Overlap (posts)" hint="An incremental scan stops after this many already-known posts in a row.">
            <input type="number" min="1" className="input" value={f.form.overlap_posts} onChange={(e) => f.set("overlap_posts", num(e.target.value, 10))} />
          </Field>
          <Field label="Full rescan every (days)" hint="Walks the whole back-catalogue again to catch edits and newly unlocked posts. 0 disables.">
            <input type="number" min="0" className="input" value={f.form.full_rescan_days} onChange={(e) => f.set("full_rescan_days", num(e.target.value, 0))} />
          </Field>
        </div>
      </Section>
      <Section title="Defaults for new creators" description="Applied when adding or importing creators; each creator can override them.">
        <div className="grid gap-3 sm:grid-cols-2">
          <Toggle checked={f.form.default_auto_download} onChange={(v) => f.set("default_auto_download", v)} label="Auto download" />
          <Toggle checked={f.form.default_include_images} onChange={(v) => f.set("default_include_images", v)} label="Archive images" />
          <Toggle checked={f.form.default_include_audio} onChange={(v) => f.set("default_include_audio", v)} label="Archive audio" />
          <Toggle checked={f.form.default_include_attachments} onChange={(v) => f.set("default_include_attachments", v)} label="Archive attachments" />
        </div>
      </Section>
      <div className="flex justify-end"><Button variant="primary" disabled={!f.dirty} loading={f.saving} onClick={f.save}>Save</Button></div>
    </div>
  );
}
