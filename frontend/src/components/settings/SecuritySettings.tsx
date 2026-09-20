import { useState } from "react";
import { useAuthState, useDisableAuth, useLogout, useSetPassword } from "../../api/hooks/useAuth";
import { Section, Field } from "../ui/Misc";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { useToast } from "../ui/Toast";

export function SecuritySettings() {
  const auth = useAuthState();
  const setPw = useSetPassword();
  const disable = useDisableAuth();
  const logout = useLogout();
  const { toast, error } = useToast();
  const [pw, setPw1] = useState("");
  const [current, setCurrent] = useState("");
  const enabled = auth.data?.auth_enabled;

  return (
    <div className="grid gap-5">
      <Section title="Web UI password" description="Require a password to open Patrearr. Recommended if the app is reachable beyond your own machine.">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-fg-muted">Status:</span>
          <Badge tone={enabled ? "ok" : "muted"}>{enabled ? "protected" : "open (no password)"}</Badge>
        </div>
        {enabled && (
          <Field label="Current password" hint="Needed to change the password.">
            <input type="password" className="input" value={current} onChange={(e) => setCurrent(e.target.value)} />
          </Field>
        )}
        <Field label={enabled ? "New password" : "Set a password"} hint="At least 6 characters.">
          <input type="password" className="input" value={pw} onChange={(e) => setPw1(e.target.value)} />
        </Field>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            disabled={pw.length < 6}
            loading={setPw.isPending}
            onClick={() => setPw.mutate({ password: pw, current_password: current || undefined }, { onSuccess: () => { toast("Password saved", "success"); setPw1(""); setCurrent(""); }, onError: (e) => error(e) })}
          >
            {enabled ? "Change password" : "Enable password"}
          </Button>
          {enabled && (
            <>
              <Button onClick={() => logout.mutate(undefined, { onSuccess: () => toast("Signed out") })}>Sign out</Button>
              <Button variant="danger" onClick={() => disable.mutate(undefined, { onSuccess: () => toast("Password protection disabled"), onError: (e) => error(e) })}>Disable protection</Button>
            </>
          )}
        </div>
      </Section>
    </div>
  );
}
