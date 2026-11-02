import { useState } from "react";
import { useSetPassword, useSkipSetup } from "../api/hooks/useAuth";
import { Button } from "../components/ui/Button";
import { MIN_PASSWORD_LENGTH } from "../components/settings/SecuritySettings";

/** First run: set a password, or knowingly go on without one. */
export function SetupPage() {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const setPw = useSetPassword();
  const skip = useSkipSetup();
  const tooShort = password.length < MIN_PASSWORD_LENGTH;
  const submit = () => {
    setError("");
    if (password !== confirm) return setError("The passwords don't match");
    setPw.mutate({ password }, { onError: (e) => setError(e instanceof Error ? e.message : "Could not save the password") });
  };
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg-0 px-4">
      <div className="card w-full max-w-md p-6">
        <div className="mb-4 flex items-center gap-2">
          <img src="/logo.png" alt="" className="h-8 w-8" />
          <span className="text-lg font-bold">Welcome to Creeparr</span>
        </div>
        <p className="mb-4 text-sm text-fg-muted">
          Creeparr stores the sessions of the accounts you connect. Set a password so only you can open it, especially if it can be reached from outside your home network.
        </p>
        <label className="label" htmlFor="setup-password">Password</label>
        <input id="setup-password" type="password" className="input" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
        <p className="mt-1 text-xs text-fg-dim">At least {MIN_PASSWORD_LENGTH} characters.</p>
        <label className="label mt-3" htmlFor="setup-confirm">Confirm password</label>
        <input id="setup-confirm" type="password" className="input" value={confirm} onChange={(e) => setConfirm(e.target.value)} onKeyDown={(e) => e.key === "Enter" && !tooShort && submit()} />
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
        <Button variant="primary" className="mt-4 w-full" disabled={tooShort} loading={setPw.isPending} onClick={submit}>
          Set password
        </Button>
        <div className="mt-5 border-t border-line pt-4">
          <Button variant="ghost" className="w-full" loading={skip.isPending} onClick={() => skip.mutate()}>
            Continue without a password
          </Button>
          <p className="mt-2 text-xs text-fg-dim">
            Only for a local network you trust: anyone who can reach Creeparr can then use it, and see your connected accounts. You can set a password later in Settings → Security.
          </p>
        </div>
      </div>
    </div>
  );
}
