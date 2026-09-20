import { Link, NavLink, Outlet } from "react-router";
import { Activity, AlertTriangle, ClipboardList, Download, FileText, Gauge, Settings, Users } from "lucide-react";
import { useServerEvents } from "../../api/events";
import { useAuthStatus, useSystemStatus } from "../../api/hooks/useSystem";
import { cx } from "../../lib/format";

const NAV = [
  { to: "/creators", label: "Creators", icon: Users },
  { group: "Activity" },
  { to: "/activity/queue", label: "Queue", icon: Download },
  { to: "/activity/history", label: "History", icon: Activity },
  { group: "Settings" },
  { to: "/settings/patreon", label: "Settings", icon: Settings, match: "/settings" },
  { group: "System" },
  { to: "/system/status", label: "Status", icon: Gauge },
  { to: "/system/logs", label: "Logs", icon: FileText },
] as const;

export function AppShell() {
  useServerEvents();
  const auth = useAuthStatus();
  const status = useSystemStatus();
  const queued = (status.data?.counts.queued ?? 0) + (status.data?.counts.running ?? 0);
  const authBad = auth && ["invalid", "challenge", "unconfigured", "error"].includes(auth.state);

  return (
    <div className="flex h-full">
      <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-bg-1">
        <Link to="/creators" className="flex items-center gap-2 px-4 py-4">
          <img src="/logo.svg" alt="" className="h-7 w-7" />
          <span className="text-base font-bold tracking-tight">Patrearr</span>
        </Link>
        <nav className="flex-1 px-2">
          {NAV.map((item, i) =>
            "group" in item ? (
              <div key={i} className="mb-1 mt-4 px-2 text-[10px] font-semibold uppercase tracking-wider text-fg-dim">{item.group}</div>
            ) : (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cx(
                    "mb-0.5 flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                    isActive || ("match" in item && location.pathname.startsWith(item.match)) ? "bg-bg-3 text-fg" : "text-fg-muted hover:bg-bg-2 hover:text-fg",
                  )
                }
              >
                <item.icon className="h-4 w-4" />
                <span className="flex-1">{item.label}</span>
                {item.to === "/activity/queue" && queued > 0 && (
                  <span className="rounded-full bg-accent px-1.5 text-[10px] font-bold text-black">{queued}</span>
                )}
              </NavLink>
            ),
          )}
        </nav>
        <div className="border-t border-line px-4 py-3 text-xs">
          <Link to="/settings/patreon" className="flex items-center gap-2">
            <span className={cx("h-2 w-2 rounded-full", auth?.state === "valid" ? "bg-ok" : authBad ? "bg-danger" : "bg-warn")} />
            <span className="truncate text-fg-muted">
              {auth?.state === "valid" ? auth.user_name ?? "Connected" : auth?.state === "unconfigured" ? "Not connected" : auth?.state ?? "…"}
            </span>
          </Link>
          <div className="mt-1 text-fg-dim">v{status.data?.version ?? "…"}</div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        {authBad && (
          <Link to="/settings/patreon" className="flex items-center gap-2 border-b border-danger/30 bg-danger/10 px-5 py-2 text-sm text-danger">
            <AlertTriangle className="h-4 w-4" />
            {auth?.state === "unconfigured"
              ? "Patreon session not configured. Add your session cookie in Settings to start archiving."
              : auth?.state === "challenge"
                ? "Cloudflare challenge blocked Patreon requests. Try the curl_cffi backend or paste a full cookies.txt in Settings."
                : `Patreon session problem: ${auth?.error ?? "invalid"}. Scans are paused until it is fixed.`}
          </Link>
        )}
        {status.data?.downloads.paused && (
          <div className="flex items-center gap-2 border-b border-warn/30 bg-warn/10 px-5 py-1.5 text-xs text-warn">
            <ClipboardList className="h-3.5 w-3.5" />
            Downloads paused{status.data.downloads.paused_reason === "disk_full" ? ": free disk space is below the configured minimum." : "."}
          </div>
        )}
        <main className="min-w-0 flex-1 overflow-y-auto px-6 py-5">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 className="text-xl font-bold tracking-tight">{title}</h1>
        {subtitle && <p className="text-sm text-fg-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
