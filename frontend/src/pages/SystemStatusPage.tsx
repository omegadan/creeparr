import { Play } from "lucide-react";
import { useRunTask, useSystemStatus, useTasks } from "../api/hooks/useSystem";
import { PageHeader } from "../components/layout/AppShell";
import { Button } from "../components/ui/Button";
import { Spinner } from "../components/ui/Misc";
import { Badge } from "../components/ui/Badge";
import { formatBytes, formatDate, formatDuration, timeAgo } from "../lib/format";
import { useToast } from "../components/ui/Toast";

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div className="card px-4 py-3">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-fg-dim">{label}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
      {sub && <div className="text-xs text-fg-muted">{sub}</div>}
    </div>
  );
}

export function SystemStatusPage() {
  const status = useSystemStatus(5000);
  const tasks = useTasks();
  const run = useRunTask();
  const { toast, error } = useToast();
  const s = status.data;
  if (!s) return <div className="flex justify-center py-20"><Spinner /></div>;
  const diskPct = s.disk.total_bytes ? Math.round(((s.disk.used_bytes ?? 0) / s.disk.total_bytes) * 100) : null;
  return (
    <>
      <PageHeader title="System status" subtitle={`Patrearr v${s.version} · up ${formatDuration(s.uptime_seconds)}`} />
      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Patreon session" value={<Badge tone={s.auth.state === "valid" ? "ok" : s.auth.state === "unknown" ? "warn" : "danger"}>{s.auth.state}</Badge>} sub={s.auth.user_name ?? s.auth.error ?? `checked ${timeAgo(s.auth.checked_at)}`} />
        <Stat label="Archive" value={`${formatBytes(s.counts.media_bytes)}`} sub={`${s.counts.media_completed} files · ${Object.entries(s.counts.provider_bytes || {}).map(([p, b]) => `${p}: ${formatBytes(b)}`).join(" · ") || `${s.counts.posts} posts`}`} />
        <Stat label="Queue" value={`${s.counts.running} running · ${s.counts.queued} queued`} sub={`${s.counts.failed} failed · ${s.downloads.workers} workers${s.downloads.paused ? ` · paused (${s.downloads.paused_reason})` : ""}`} />
        <Stat label="Disk" value={formatBytes(s.disk.free_bytes) + " free"} sub={diskPct !== null ? `${diskPct}% used of ${formatBytes(s.disk.total_bytes)}` : undefined} />
        <Stat label="Next scan" value={s.next_scan_at ? timeAgo(s.next_scan_at).replace("in ", "in ") : "disabled"} sub={s.scan.running ? `scanning creator #${s.scan.running.creator_id}` : `${s.scan.pending.length} pending`} />
        <Stat label="yt-dlp" value={s.ytdlp_version} sub={s.ffmpeg ? `ffmpeg: ${s.ffmpeg}` : "ffmpeg NOT FOUND"} />
        <Stat label="HTTP backend" value={s.http_backend} sub={s.curl_cffi_available ? "curl_cffi available" : "curl_cffi not installed"} />
        <Stat label="Paths" value={<span className="font-mono text-sm">{s.paths.download_dir}</span>} sub={`config: ${s.paths.config_dir}${s.paths.onlyfans_download_dir ? ` · onlyfans: ${s.paths.onlyfans_download_dir}` : ""}${s.paths.youtube_download_dir ? ` · youtube: ${s.paths.youtube_download_dir}` : ""}${s.paths.instagram_download_dir ? ` · instagram: ${s.paths.instagram_download_dir}` : ""}${s.paths.reddit_download_dir ? ` · reddit: ${s.paths.reddit_download_dir}` : ""}`} />
      </div>
      <h2 className="mb-2 text-sm font-semibold text-fg-muted">Scheduled tasks</h2>
      <div className="card overflow-x-auto">
        <table className="table">
          <thead><tr><th>Task</th><th>Interval</th><th>Last run</th><th>Next run</th><th>Result</th><th className="text-right"></th></tr></thead>
          <tbody>
            {(tasks.data ?? []).map((t) => (
              <tr key={t.name}>
                <td><div className="font-medium">{t.name}</div><div className="text-xs text-fg-dim">{t.description}</div></td>
                <td className="text-fg-muted">{t.interval_seconds ? formatDuration(t.interval_seconds) : "disabled"}</td>
                <td className="text-fg-muted">{t.last_run_at ? formatDate(t.last_run_at, true) : "never"}</td>
                <td className="text-fg-muted">{t.next_run_at ? timeAgo(t.next_run_at) : "–"}</td>
                <td>{t.running ? <Badge tone="info">running</Badge> : t.last_status ? <Badge tone={t.last_status === "ok" ? "ok" : t.last_status === "skipped" ? "warn" : "danger"} title={t.last_error ?? ""}>{t.last_status}</Badge> : "–"}</td>
                <td className="text-right"><Button size="sm" icon={<Play className="h-3.5 w-3.5" />} disabled={t.running} onClick={() => run.mutate(t.name, { onSuccess: () => toast(`Started ${t.name}`), onError: (e) => error(e) })}>Run</Button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
