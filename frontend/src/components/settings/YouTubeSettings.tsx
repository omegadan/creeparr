import type { Settings } from "../../api/types";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { num, useSettingsForm } from "./useSettingsForm";

export function YouTubeSettings({ settings }: { settings: Settings }) {
  const f = useSettingsForm(settings, "youtube");
  return (
    <div className="grid gap-5">
      <Section title="YouTube" description="Public channels need no login. Add a channel in Add creator by URL, @handle or channel id; it back-fills the channel's videos and monitors for new ones.">
        <Field label="Max videos per channel" hint="0 archives the entire channel; set a limit for very large channels.">
          <input type="number" min="0" className="input" value={f.form.max_videos} onChange={(e) => f.set("max_videos", num(e.target.value, 0))} />
        </Field>
        <div className="flex justify-end"><Button variant="primary" disabled={!f.dirty} loading={f.saving} onClick={f.save}>Save</Button></div>
      </Section>
    </div>
  );
}
