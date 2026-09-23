import { ExternalLink, FileText, Image, Music, RotateCcw, SkipForward, Undo2, Video } from "lucide-react";
import { usePost, useRetryMedia, useSkipMedia, useUnskipMedia } from "../../api/hooks/usePosts";
import type { MediaItem } from "../../api/types";
import { mediaStatus, sourceLabel } from "../../lib/status";
import { formatBytes, formatDate, timeAgo } from "../../lib/format";
import { StatusBadge } from "../ui/Badge";
import { IconButton } from "../ui/Button";
import { Spinner } from "../ui/Misc";
import { useToast } from "../ui/Toast";

const KIND_ICON = { video: Video, image: Image, audio: Music, attachment: FileText };

export function MediaItemList({ postId }: { postId: number }) {
  const post = usePost(postId);
  const retry = useRetryMedia();
  const skip = useSkipMedia();
  const unskip = useUnskipMedia();
  const { error } = useToast();

  if (post.isLoading) return <div className="flex justify-center py-4"><Spinner /></div>;
  const items = post.data?.media_items ?? [];
  return (
    <div className="px-4 py-3">
      {post.data?.teaser_text && <p className="mb-3 line-clamp-2 text-xs text-fg-muted">{post.data.teaser_text}</p>}
      {post.data?.folder_path && <p className="mb-2 font-mono text-[11px] text-fg-dim">{post.data.folder_path}</p>}
      {items.length === 0 ? (
        <div className="text-xs text-fg-muted">{post.data?.current_user_can_view ? "No downloadable media in this post." : "This post is not accessible with your current pledge."}</div>
      ) : (
        <table className="table text-xs">
          <thead>
            <tr>
              <th className="w-8"></th>
              <th>File</th>
              <th className="w-28">Source</th>
              <th className="w-32">Status</th>
              <th className="w-24">Size</th>
              <th>Detail</th>
              <th className="w-20 text-right"></th>
            </tr>
          </thead>
          <tbody>
            {items.map((m: MediaItem) => {
              const Icon = KIND_ICON[m.kind] ?? FileText;
              const canRetry = ["failed", "failed_permanent", "cancelled", "unsupported", "unsupported_drm", "skipped", "discovered", "completed", "missing"].includes(m.status);
              return (
                <tr key={m.id}>
                  <td className="text-fg-dim"><Icon className="h-4 w-4" /></td>
                  <td className="font-mono">
                    <div className="flex items-center gap-2">
                      {m.status === "completed" && m.kind === "image" && (
                        <a href={`/api/v1/media/${m.id}/file`} target="_blank" rel="noreferrer">
                          <img src={`/api/v1/media/${m.id}/file`} alt="" loading="lazy" decoding="async" className="h-9 w-9 rounded object-cover" />
                        </a>
                      )}
                      <span>{m.file_path ? m.file_path.split("/").pop() : m.remote_file_name ?? `${m.kind} ${m.order_index}`}</span>
                      {m.status === "completed" && m.kind !== "image" && (
                        <a href={`/api/v1/media/${m.id}/file`} target="_blank" rel="noreferrer" title="Open file" className="text-fg-dim hover:text-fg"><ExternalLink className="h-3.5 w-3.5" /></a>
                      )}
                    </div>
                  </td>
                  <td className="text-fg-muted">{sourceLabel(m.source)}</td>
                  <td><StatusBadge meta={mediaStatus(m.status)} title={m.status_reason ?? undefined} /></td>
                  <td className="text-fg-muted">{formatBytes(m.file_size_bytes ?? m.remote_size_bytes)}</td>
                  <td className="max-w-md truncate text-fg-muted" title={m.last_error ?? m.status_reason ?? ""}>
                    {m.status === "completed" ? `archived ${formatDate(m.completed_at, true)}` : m.status === "failed" && m.next_retry_at ? `attempt ${m.attempts} · retry ${timeAgo(m.next_retry_at)} · ${m.last_error ?? ""}` : m.last_error ?? m.status_reason ?? ""}
                  </td>
                  <td className="text-right">
                    <div className="flex justify-end gap-0.5">
                      {canRetry && (
                        <IconButton title={m.status === "completed" || m.status === "missing" ? "Re-download" : "Retry now"} onClick={() => retry.mutate(m.id, { onError: (e) => error(e) })}>
                          <RotateCcw className="h-3.5 w-3.5" />
                        </IconButton>
                      )}
                      {m.status === "skipped" || !m.wanted ? (
                        <IconButton title="Unskip" onClick={() => unskip.mutate(m.id, { onError: (e) => error(e) })}><Undo2 className="h-3.5 w-3.5" /></IconButton>
                      ) : m.status !== "completed" ? (
                        <IconButton title="Skip" onClick={() => skip.mutate(m.id, { onError: (e) => error(e) })}><SkipForward className="h-3.5 w-3.5" /></IconButton>
                      ) : null}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
