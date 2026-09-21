import type { ProviderQueueStatus } from "../../api/types";
import { Badge } from "../ui/Badge";
import type { StatusMeta } from "../../lib/status";

const META: Record<ProviderQueueStatus["state"], { label: string; tone: StatusMeta["tone"] }> = {
  downloading: { label: "Downloading", tone: "ok" },
  waiting: { label: "Waiting", tone: "info" },
  throttled: { label: "Rate-limited", tone: "warn" },
  blocked: { label: "Auth error", tone: "danger" },
  disabled: { label: "Disabled", tone: "muted" },
  paused: { label: "Paused", tone: "warn" },
  idle: { label: "Idle", tone: "muted" },
};

function humanDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}

function detail(p: ProviderQueueStatus): string {
  const queued = p.queued > 0 ? `${p.queued} queued` : "nothing queued";
  switch (p.state) {
    case "downloading":
      return `${p.running} downloading${p.queued > 0 ? ` · ${p.queued} queued` : ""}`;
    case "throttled": {
      const limit = `${p.hourly_limit}/hr limit`;
      const next = p.next_slot_seconds != null ? ` · next in ${humanDuration(p.next_slot_seconds)}` : "";
      return `${p.queued} queued · ${limit}${next}`;
    }
    case "waiting":
      return `${queued} · starting soon`;
    case "blocked":
      return `${queued} · sign-in required`;
    case "disabled":
      return "provider turned off";
    case "paused":
      return `${queued} · queue paused`;
    default:
      return queued;
  }
}

export function ProviderQueueStatusPanel({ providers }: { providers: ProviderQueueStatus[] }) {
  if (!providers.length) return null;
  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold text-fg-muted">Providers</h2>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {providers.map((p) => {
          const meta = META[p.state] ?? META.idle;
          return (
            <div key={p.provider} className="card flex items-center justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="truncate font-medium">{p.label}</div>
                <div className="truncate text-xs text-fg-muted">{detail(p)}</div>
              </div>
              <Badge tone={meta.tone}>{meta.label}</Badge>
            </div>
          );
        })}
      </div>
    </div>
  );
}
