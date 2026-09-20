import type { Settings } from "../../api/types";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { Toggle } from "../ui/Toggle";
import { num, useSettingsForm } from "./useSettingsForm";

export function DownloadSettings({ settings }: { settings: Settings }) {
  const f = useSettingsForm(settings, "downloads");
  return (
    <div className="grid gap-5">
      <Section title="Workers" description="Parallelism and disk protection.">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Concurrent downloads" hint="1–8">
            <input type="number" min="1" max="8" className="input" value={f.form.concurrency} onChange={(e) => f.set("concurrency", num(e.target.value, 2))} />
          </Field>
          <Field label="Max per creator" hint="Keeps one creator from hogging all workers.">
            <input type="number" min="1" max="8" className="input" value={f.form.max_per_creator} onChange={(e) => f.set("max_per_creator", num(e.target.value, 2))} />
          </Field>
          <Field label="Minimum free space (MB)" hint="Downloads pause below this.">
            <input type="number" min="0" className="input" value={f.form.min_free_mb} onChange={(e) => f.set("min_free_mb", num(e.target.value, 1024))} />
          </Field>
        </div>
      </Section>
      <Section title="Retries" description="Transient failures are retried with exponential back-off. DRM, deleted and inaccessible media are never retried automatically.">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Max attempts"><input type="number" min="1" className="input" value={f.form.max_attempts} onChange={(e) => f.set("max_attempts", num(e.target.value, 5))} /></Field>
          <Field label="First retry after (s)"><input type="number" min="5" className="input" value={f.form.retry_base_seconds} onChange={(e) => f.set("retry_base_seconds", num(e.target.value, 60))} /></Field>
          <Field label="Max back-off (s)"><input type="number" min="60" className="input" value={f.form.retry_cap_seconds} onChange={(e) => f.set("retry_cap_seconds", num(e.target.value, 21600))} /></Field>
          <Field label="Refresh URLs older than (min)" hint="Patreon media links are signed and expire.">
            <input type="number" min="1" className="input" value={f.form.url_max_age_minutes} onChange={(e) => f.set("url_max_age_minutes", num(e.target.value, 30))} />
          </Field>
        </div>
      </Section>
      <Section title="Video (yt-dlp)" description="Used for Patreon streams and YouTube/Vimeo embeds.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Format selector" hint="yt-dlp -f syntax. Default picks best video + best audio, merged to mp4.">
            <input className="input font-mono" value={f.form.video_format} onChange={(e) => f.set("video_format", e.target.value)} />
          </Field>
          <Field label="HLS fragment concurrency"><input type="number" min="1" max="16" className="input" value={f.form.hls_fragment_concurrency} onChange={(e) => f.set("hls_fragment_concurrency", num(e.target.value, 4))} /></Field>
        </div>
        <Toggle checked={f.form.ytdlp_remote_components} onChange={(v) => f.set("ytdlp_remote_components", v)} label="Allow yt-dlp to fetch its YouTube challenge-solver script from GitHub" hint="Needed for full YouTube format availability; the script runs in the local JS runtime (node/deno)." />
        <Toggle checked={f.form.compute_sha256} onChange={(v) => f.set("compute_sha256", v)} label="Record SHA-256 of archived files" hint="Costs a little CPU; useful for integrity checks." />
      </Section>
      <div className="flex justify-end"><Button variant="primary" disabled={!f.dirty} loading={f.saving} onClick={f.save}>Save</Button></div>
    </div>
  );
}
