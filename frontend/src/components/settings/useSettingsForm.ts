import { useState } from "react";
import { useUpdateSettings } from "../../api/hooks/useSettings";
import type { Settings, SettingsPatch } from "../../api/types";
import { useToast } from "../ui/Toast";
import { rebaseForm } from "./rebaseForm";

type Group = Exclude<keyof Settings, "env">;

/** Local editable copy of one settings group with a save() that PUTs only that group. */
export function useSettingsForm<G extends Group>(settings: Settings, group: G) {
  const server = settings[group];
  const [form, setForm] = useState<Settings[G]>(server);
  const [base, setBase] = useState<Settings[G]>(server);
  // New server values (another section saved, a toggle, a refetch): adopt them, but keep
  // any field edited here and not yet saved. Adjusting state during render is React's
  // recommended way to follow a changed prop.
  if (server !== base) {
    setBase(server);
    setForm((f) => rebaseForm(f, base, server));
  }
  const update = useUpdateSettings();
  const { toast, error } = useToast();
  const set = <K extends keyof Settings[G]>(key: K, value: Settings[G][K]) => setForm((f) => ({ ...f, [key]: value }));
  const dirty = JSON.stringify(form) !== JSON.stringify(settings[group]);
  const save = () =>
    update.mutate({ [group]: form } as SettingsPatch, { onSuccess: () => toast("Settings saved", "success"), onError: (e) => error(e, "Could not save settings") });
  return { form, set, save, dirty, saving: update.isPending };
}

export function num(v: string, fallback: number): number {
  if (v.trim() === "") return fallback; // a cleared field, not 0
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}
