import type { CreatorDefaults } from "../../api/types";
import { Toggle } from "../ui/Toggle";
import { Field } from "../ui/Misc";

interface Props {
  value: CreatorDefaults;
  onChange: (v: CreatorDefaults) => void;
  showFolder?: boolean;
  compact?: boolean;
}

export function CreatorOptions({ value, onChange, showFolder, compact }: Props) {
  const set = (k: keyof CreatorDefaults, v: unknown) => onChange({ ...value, [k]: v });
  return (
    <div className={compact ? "grid gap-3 sm:grid-cols-2" : "grid gap-3"}>
      <Toggle checked={value.monitored ?? true} onChange={(v) => set("monitored", v)} label="Monitored" hint="Include in scheduled scans" />
      <Toggle checked={value.auto_download ?? true} onChange={(v) => set("auto_download", v)} label="Auto download" hint="Queue new media automatically" />
      <Toggle checked={value.include_images ?? false} onChange={(v) => set("include_images", v)} label="Images" hint="Archive image galleries" />
      <Toggle checked={value.include_audio ?? false} onChange={(v) => set("include_audio", v)} label="Audio" hint="Archive audio posts and podcasts" />
      <Toggle checked={value.include_attachments ?? false} onChange={(v) => set("include_attachments", v)} label="Attachments" hint="Archive attached files" />
      <Field label="Only download posts published after" hint="Leave empty for the full back-catalogue" className={compact ? "" : "mt-1"}>
        <input
          type="date"
          className="input"
          value={value.download_since ? value.download_since.slice(0, 10) : ""}
          onChange={(e) => set("download_since", e.target.value ? new Date(e.target.value).toISOString() : null)}
        />
      </Field>
      {showFolder && (
        <>
          <Field label="Scan interval override (minutes)" hint="Blank uses the global interval; 0 never auto-scans.">
            <input type="number" min="0" className="input" value={value.scan_interval_minutes ?? ""} placeholder="(global)" onChange={(e) => set("scan_interval_minutes", e.target.value === "" ? null : Number(e.target.value))} />
          </Field>
          <Field label="Folder name override" hint="Used for {creator} in the naming template">
            <input className="input" value={value.folder_name ?? ""} placeholder="(creator name)" onChange={(e) => set("folder_name", e.target.value)} />
          </Field>
        </>
      )}
    </div>
  );
}
