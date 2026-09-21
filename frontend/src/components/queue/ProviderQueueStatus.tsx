import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
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
  creators_off: { label: "Creators off", tone: "muted" },
  idle: { label: "Idle", tone: "muted" },
};

const DOT_CLASS: Record<ProviderQueueStatus["state"], string> = {
  downloading: "bg-ok",
  waiting: "bg-info",
  throttled: "bg-warn",
  blocked: "bg-danger",
  disabled: "bg-fg-dim",
  paused: "bg-warn",
  creators_off: "bg-fg-dim",
  idle: "bg-fg-dim",
};

function humanDuration(seconds: number): string {
  if (seconds <= 0) return "now";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}

/** Re-render on an interval while `active`, so countdowns tick without a refetch. */
function useTicker(active: boolean, ms = 1000): void {
  const [, force] = useState(0);
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => force((n) => n + 1), ms);
    return () => window.clearInterval(id);
  }, [active, ms]);
}

function remainingSeconds(p: ProviderQueueStatus, nowMs: number): number | null {
  if (p.next_slot_at) return Math.max(0, Math.round((new Date(p.next_slot_at).getTime() - nowMs) / 1000));
  if (p.next_slot_seconds != null) return Math.max(0, p.next_slot_seconds);
  return null;
}

function detail(p: ProviderQueueStatus, nowMs: number): string {
  const queued = p.queued > 0 ? `${p.queued} queued` : "nothing queued";
  switch (p.state) {
    case "downloading":
      return `${p.running} downloading${p.queued > 0 ? ` · ${p.queued} queued` : ""}`;
    case "throttled": {
      const rem = remainingSeconds(p, nowMs);
      const next = rem != null ? ` · next in ${humanDuration(rem)}` : "";
      return `${p.queued} queued · ${p.hourly_limit}/hr limit${next}`;
    }
    case "waiting":
      return `${queued} · starting soon`;
    case "blocked":
      return `${queued} · sign-in required`;
    case "disabled":
      return "provider turned off";
    case "creators_off":
      return `${p.queued} queued · all creators disabled`;
    case "paused":
      return `${queued} · queue paused`;
    default:
      return queued;
  }
}

export function ProviderQueueStatusPanel({ providers }: { providers: ProviderQueueStatus[] }) {
  const qc = useQueryClient();
  const nowMs = Date.now();
  // Tick every second while a countdown is showing so it stays live between refetches.
  const anyCountdown = providers.some((p) => p.state === "throttled" && remainingSeconds(p, nowMs) != null);
  useTicker(anyCountdown);

  // When a rate-limit window elapses, pull a fresh queue so the state advances
  // promptly instead of waiting for the next poll. Guard so we only fire once.
  const refetchedFor = useRef<string>("");
  useEffect(() => {
    const due = providers.some(
      (p) => p.state === "throttled" && p.next_slot_at && new Date(p.next_slot_at).getTime() <= Date.now(),
    );
    if (!due) return;
    const key = providers.map((p) => `${p.provider}:${p.next_slot_at ?? ""}`).join("|");
    if (refetchedFor.current === key) return;
    refetchedFor.current = key;
    qc.invalidateQueries({ queryKey: ["queue"] });
  });

  if (!providers.length) return null;
  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold text-fg-muted">Providers</h2>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {providers.map((p) => {
          const meta = META[p.state] ?? META.idle;
          return (
            <div key={p.provider} className="card flex items-center justify-between gap-3 p-3">
              <div className="flex min-w-0 items-center gap-2.5">
                <span className="relative flex h-2.5 w-2.5 shrink-0">
                  {p.state === "downloading" && (
                    <span className={`absolute inline-flex h-full w-full animate-ping rounded-full ${DOT_CLASS[p.state]} opacity-75`} />
                  )}
                  <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${DOT_CLASS[p.state]}`} />
                </span>
                <div className="min-w-0">
                  <div className="truncate font-medium">{p.label}</div>
                  <div className="truncate text-xs text-fg-muted tabular-nums">{detail(p, nowMs)}</div>
                </div>
              </div>
              <Badge tone={meta.tone}>{meta.label}</Badge>
            </div>
          );
        })}
      </div>
    </div>
  );
}
