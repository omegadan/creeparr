import { useState } from "react";
import type { Settings } from "../../api/types";
import { useUpdateSettings } from "../../api/hooks/useSettings";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { useToast } from "../ui/Toast";
import { num } from "./useSettingsForm";
import { ProviderAccount } from "./ProviderAccount";

/** The tab of a provider with no hand-made form: its account and its hourly limit. */
export function GenericProviderSettings({ name, settings }: { name: string; settings: Settings }) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const stored = ((settings as any)[name]?.downloads_per_hour ?? 0) as number;
  const [perHour, setPerHour] = useState(stored);
  const update = useUpdateSettings();
  const { toast, error } = useToast();
  const save = () =>
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    update.mutate({ [name]: { downloads_per_hour: perHour } } as any, { onSuccess: () => toast("Settings saved", "success"), onError: (e) => error(e, "Could not save settings") });
  return (
    <div className="grid gap-5">
      <ProviderAccount name={name} settings={settings} />
      <Section title="Downloads">
        <Field label="Downloads per hour" hint="Cap how many downloads start per hour; spaced out at random (Downloads → Pacing). 0 = unlimited.">
          <input type="number" min="0" className="input" value={perHour} onChange={(e) => setPerHour(num(e.target.value, 0))} />
        </Field>
        <div className="flex justify-end"><Button variant="primary" disabled={perHour === stored} loading={update.isPending} onClick={save}>Save</Button></div>
      </Section>
    </div>
  );
}
