import { useEffect } from "react";
import { Outlet } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useAuthState } from "./api/hooks/useAuth";
import { LoginPage } from "./pages/LoginPage";
import { Spinner } from "./components/ui/Misc";

export function App() {
  const auth = useAuthState();
  const qc = useQueryClient();
  useEffect(() => {
    const onUnauth = () => qc.invalidateQueries({ queryKey: ["authState"] });
    window.addEventListener("creeparr-unauthorized", onUnauth);
    return () => window.removeEventListener("creeparr-unauthorized", onUnauth);
  }, [qc]);
  if (auth.isLoading) return <div className="flex min-h-screen items-center justify-center"><Spinner /></div>;
  if (auth.data?.auth_enabled && !auth.data.authenticated) return <LoginPage />;
  return <Outlet />;
}
