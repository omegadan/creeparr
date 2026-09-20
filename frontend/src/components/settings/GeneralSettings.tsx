import { useEffect } from "react";
import type { Settings } from "../../api/types";
import { useNamingPreview } from "../../api/hooks/useSettings";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { Toggle } from "../ui/Toggle";
import { num, useSettingsForm } from "./useSettingsForm";

const TOKENS = ["creator", "creator_vanity", "campaign_id", "title", "post_id", "published:%Y-%m-%d", "post_type", "filename", "ext", "media_kind", "media_index", "embed_provider"];

export function GeneralSettings({ settings }: { settings: Settings }) {
  const naming = useSettingsForm(settings, "naming");
  const history = useSettingsForm(settings, "history");
  const preview = useNamingPreview();
  const { post_folder_template, file_template } = naming.form;
  useEffect(() => {
    const t = window.setTimeout(() => preview.mutate({ post_folder_template, file_template }), 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [post_folder_template, file_template]);

  return (
    <div className="grid gap-5">
      <Section title="File naming" description="Where archived files go under the download folder. Values are sanitised; slashes in templates create folders.">
        <Field label="Post folder template">
          <input className="input font-mono" value={naming.form.post_folder_template} onChange={(e) => naming.set("post_folder_template", e.target.value)} />
        </Field>
        <Field label="File name template" hint="{filename} is the original name, or the post title for streams and embeds.">
          <input className="input font-mono" value={naming.form.file_template} onChange={(e) => naming.set("file_template", e.target.value)} />
        </Field>
        <div className="rounded-md border border-line bg-bg-0 px-3 py-2 font-mono text-xs">
          <span className="text-fg-dim">preview: </span>
          {preview.data ? preview.data.full_path : preview.isError ? <span className="text-danger">invalid template</span> : "…"}
        </div>
        <div className="flex flex-wrap gap-1">
          {TOKENS.map((t) => <code key={t} className="rounded bg-bg-3 px-1.5 py-0.5 text-[11px] text-fg-muted">{`{${t}}`}</code>)}
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Max path component length (bytes)"><input type="number" min="20" max="240" className="input" value={naming.form.max_component_length} onChange={(e) => naming.set("max_component_length", num(e.target.value, 150))} /></Field>
          <div className="pt-6"><Toggle checked={naming.form.write_sidecars} onChange={(v) => naming.set("write_sidecars", v)} label="Write post.json / post.md / post.html sidecars" /></div>
        </div>
        <div className="flex justify-end"><Button variant="primary" disabled={!naming.dirty} loading={naming.saving} onClick={naming.save}>Save naming</Button></div>
      </Section>
      <Section title="Housekeeping">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Keep history for (days)"><input type="number" min="1" className="input" value={history.form.retention_days} onChange={(e) => history.set("retention_days", num(e.target.value, 90))} /></Field>
          <Field label="Keep finished jobs for (days)"><input type="number" min="1" className="input" value={history.form.job_retention_days} onChange={(e) => history.set("job_retention_days", num(e.target.value, 30))} /></Field>
        </div>
        <div className="flex justify-end"><Button variant="primary" disabled={!history.dirty} loading={history.saving} onClick={history.save}>Save</Button></div>
      </Section>
      <Section title="Environment (read-only)" description="Set through environment variables on the container.">
        <dl className="grid grid-cols-[10rem_1fr] gap-y-1 font-mono text-xs">
          <dt className="text-fg-dim">config_dir</dt><dd>{settings.env.config_dir}</dd>
          <dt className="text-fg-dim">download_dir</dt><dd>{settings.env.download_dir}</dd>
          <dt className="text-fg-dim">port</dt><dd>{settings.env.port}</dd>
          <dt className="text-fg-dim">log_level</dt><dd>{settings.env.log_level}</dd>
          <dt className="text-fg-dim">ffmpeg</dt><dd>{settings.env.ffmpeg ?? <span className="text-danger">not found</span>}</dd>
        </dl>
      </Section>
    </div>
  );
}
