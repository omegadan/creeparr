import { useState } from "react";
import { useLogin } from "../api/hooks/useAuth";
import { Button } from "../components/ui/Button";

export function LoginPage() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const login = useLogin();
  const submit = () => {
    setError("");
    login.mutate(password, { onError: () => setError("Incorrect password") });
  };
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg-0 px-4">
      <div className="card w-full max-w-sm p-6">
        <div className="mb-5 flex items-center gap-2">
          <img src="/logo.png" alt="" className="h-8 w-8" />
          <span className="text-lg font-bold">Creeparr</span>
        </div>
        <label className="label" htmlFor="login-password">Password</label>
        <input
          id="login-password"
          type="password"
          className="input"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
        <Button variant="primary" className="mt-4 w-full" loading={login.isPending} onClick={submit}>
          Sign in
        </Button>
      </div>
    </div>
  );
}
