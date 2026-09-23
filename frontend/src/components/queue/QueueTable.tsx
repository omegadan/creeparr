import { RotateCcw, Trash2, X } from "lucide-react";
import type { FailedMedia, Job } from "../../api/types";
import { useRemoveJob, useRetryJob } from "../../api/hooks/useQueue";
import { useRetryMedia, useSkipMedia } from "../../api/hooks/usePosts";
import { formatBytes, formatDuration, formatSpeed, timeAgo } from "../../lib/format";
import { mediaStatus, sourceLabel } from "../../lib/status";
import { ProgressBar } from "../ui/ProgressBar";
import { Badge, StatusBadge } from "../ui/Badge";
import { IconButton } from "../ui/Button";
import { useToast } from "../ui/Toast";
import { Link } from "react-router";

export function JobTable({ jobs }: { jobs: Job[] }) {
  const remove = useRemoveJob();
  const retry = useRetryJob();
  const { error } = useToast();
  return (
    <div className="card overflow-x-auto">
      <table className="table">
        <thead>
          <tr>
            <th>Title</th>
            <th className="w-40">Creator</th>
            <th className="w-28">Source</th>
            <th className="w-64">Progress</th>
            <th className="w-40">Speed / ETA</th>
            <th className="w-20 text-right"></th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id}>
              <td>
                <div className="line-clamp-1 font-medium">{j.post_title ?? j.file_name ?? `job ${j.id}`}</div>
                <div className="text-[11px] text-fg-dim">{j.media_kind} · attempt {j.attempt}{j.priority > 0 ? " · manual" : ""}</div>
              </td>
              <td><Link to={`/creators/${j.creator_id}`} className="text-fg-muted hover:text-fg">{j.creator_name}</Link></td>
              <td className="text-fg-muted">{sourceLabel(j.source)}</td>
              <td>
                {j.status === "running" ? (
                  <div>
                    <div className="mb-1 flex justify-between text-[11px] text-fg-muted">
                      <span>{j.stage ?? "starting"}{j.progress_percent != null ? ` · ${j.progress_percent.toFixed(1)}%` : ""}</span>
                      <span>{formatBytes(j.bytes_downloaded)}{j.total_bytes ? ` / ${formatBytes(j.total_bytes)}` : ""}</span>
                    </div>
                    <ProgressBar value={j.progress_percent ?? 0} tone="info" indeterminate={j.progress_percent == null} />
                  </div>
                ) : (
                  <Badge tone="muted">queued {timeAgo(j.created_at)}</Badge>
                )}
              </td>
              <td className="text-xs text-fg-muted">{j.status === "running" ? `${formatSpeed(j.speed_bps)} · ${formatDuration(j.eta_seconds)}` : "–"}</td>
              <td className="text-right">
                <div className="flex justify-end gap-0.5">
                  {j.status === "failed" && <IconButton title="Retry" onClick={() => retry.mutate(j.id, { onError: (e) => error(e) })}><RotateCcw className="h-4 w-4" /></IconButton>}
                  <IconButton title={j.status === "running" ? "Cancel" : "Remove from queue"} onClick={() => remove.mutate({ id: j.id, skip: false }, { onError: (e) => error(e) })}>
                    <X className="h-4 w-4" />
                  </IconButton>
                </div>
              </td>
            </tr>
          ))}
          {jobs.length === 0 && <tr><td colSpan={6} className="py-8 text-center text-fg-muted">Nothing queued. Scans will add new media automatically.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

export function FailedTable({ items }: { items: FailedMedia[] }) {
  const retry = useRetryMedia();
  const skip = useSkipMedia();
  const { error } = useToast();
  return (
    <div className="card overflow-x-auto">
      <table className="table">
        <thead>
          <tr>
            <th>Title</th>
            <th className="w-40">Creator</th>
            <th className="w-28">Source</th>
            <th className="w-36">Status</th>
            <th>Error</th>
            <th className="w-20 text-right"></th>
          </tr>
        </thead>
        <tbody>
          {items.map((m) => (
            <tr key={m.media_item_id}>
              <td><div className="line-clamp-1 font-medium">{m.post_title}</div><div className="text-[11px] text-fg-dim">{m.media_kind} · {m.attempts} attempts</div></td>
              <td><Link to={`/creators/${m.creator_id}`} className="text-fg-muted hover:text-fg">{m.creator_name}</Link></td>
              <td className="text-fg-muted">{sourceLabel(m.source)}</td>
              <td>
                <StatusBadge meta={mediaStatus(m.status)} />
                {m.next_retry_at && <div className="text-[11px] text-fg-dim">retry {timeAgo(m.next_retry_at)}</div>}
              </td>
              <td className="max-w-md truncate text-xs text-fg-muted" title={m.last_error ?? ""}>{m.last_error ?? m.status_reason}</td>
              <td className="text-right">
                <div className="flex justify-end gap-0.5">
                  <IconButton title="Retry now" onClick={() => retry.mutate(m.media_item_id, { onError: (e) => error(e) })}><RotateCcw className="h-4 w-4" /></IconButton>
                  <IconButton title="Skip" onClick={() => skip.mutate(m.media_item_id, { onError: (e) => error(e) })}><Trash2 className="h-4 w-4" /></IconButton>
                </div>
              </td>
            </tr>
          ))}
          {items.length === 0 && <tr><td colSpan={6} className="py-6 text-center text-fg-muted">No failed downloads.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
