import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight, Download, ExternalLink, RefreshCw, SkipForward, Undo2 } from "lucide-react";
import type { Post } from "../../api/types";
import { useDownloadPost, useRefreshPost, useSkipPost, useUnskipPost } from "../../api/hooks/usePosts";
import { postStatus, postTypeLabel } from "../../lib/status";
import { formatDate, cx } from "../../lib/format";
import { StatusBadge } from "../ui/Badge";
import { IconButton } from "../ui/Button";
import { MediaItemList } from "./MediaItemList";
import { useToast } from "../ui/Toast";

export function PostTable({ posts, showCreator }: { posts: Post[]; showCreator?: boolean }) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const download = useDownloadPost();
  const skip = useSkipPost();
  const unskip = useUnskipPost();
  const refresh = useRefreshPost();
  const { toast, error } = useToast();

  const toggle = (id: number) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  return (
    <div className="card overflow-x-auto">
      <table className="table">
        <thead>
          <tr>
            <th className="w-8"></th>
            <th className="w-28">Date</th>
            {showCreator && <th>Creator</th>}
            <th>Title</th>
            <th className="w-24">Type</th>
            <th className="w-28">Status</th>
            <th className="w-28">Files</th>
            <th className="w-32 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {posts.map((p) => {
            const ms = p.media_summary;
            const expanded = open.has(p.id);
            return (
              <Fragment key={p.id}>
                <tr
                  className={cx("cursor-pointer", expanded && "bg-bg-2/40")}
                  onClick={() => toggle(p.id)}
                  tabIndex={0}
                  aria-expanded={expanded}
                  onKeyDown={(e) => {
                    if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
                      e.preventDefault();
                      toggle(p.id);
                    }
                  }}
                >
                  <td className="text-fg-dim">{expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}</td>
                  <td className="whitespace-nowrap text-fg-muted">{formatDate(p.published_at)}</td>
                  {showCreator && <td className="text-fg-muted">{p.creator_name}</td>}
                  <td>
                    <div className="flex items-center gap-2">
                      {p.thumbnail_url && <img src={p.thumbnail_url} alt="" className="h-8 w-12 rounded object-cover" referrerPolicy="no-referrer" />}
                      <span className="line-clamp-1 font-medium">{p.title || "(untitled)"}</span>
                      {p.url && (
                        <a href={p.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="text-fg-dim hover:text-fg" title="Open the original post">
                          <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                      )}
                    </div>
                  </td>
                  <td className="text-fg-muted">{postTypeLabel(p.post_type)}{p.embed_provider ? ` · ${p.embed_provider}` : ""}</td>
                  <td><StatusBadge meta={postStatus(p.status)} title={p.status_reason ?? undefined} /></td>
                  <td className="text-xs text-fg-muted">
                    {ms.total === 0 ? "–" : (
                      <span>
                        <span className={ms.completed === ms.total ? "text-ok" : ""}>{ms.completed}</span>/{ms.total}
                        {ms.failed > 0 && <span className="text-danger"> · {ms.failed} failed</span>}
                        {ms.unsupported > 0 && <span className="text-danger"> · {ms.unsupported} DRM</span>}
                      </span>
                    )}
                  </td>
                  <td className="text-right" onClick={(e) => e.stopPropagation()}>
                    <div className="flex justify-end gap-0.5">
                      <IconButton title="Download / re-queue" onClick={() => download.mutate({ id: p.id, body: { force: p.status === "completed" } }, { onError: (e) => error(e), onSuccess: () => toast("Queued", "success") })}>
                        <Download className="h-4 w-4" />
                      </IconButton>
                      {p.status === "skipped" ? (
                        <IconButton title="Unskip" onClick={() => unskip.mutate({ id: p.id }, { onError: (e) => error(e) })}><Undo2 className="h-4 w-4" /></IconButton>
                      ) : (
                        <IconButton title="Skip this post" onClick={() => skip.mutate({ id: p.id }, { onError: (e) => error(e) })}><SkipForward className="h-4 w-4" /></IconButton>
                      )}
                      <IconButton title="Refresh from the source" onClick={() => refresh.mutate({ id: p.id }, { onError: (e) => error(e), onSuccess: () => toast("Post refreshed", "success") })}>
                        <RefreshCw className="h-4 w-4" />
                      </IconButton>
                    </div>
                  </td>
                </tr>
                {expanded && (
                  <tr>
                    <td colSpan={showCreator ? 8 : 7} className="bg-bg-0/40 p-0">
                      <MediaItemList postId={p.id} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
          {posts.length === 0 && (
            <tr><td colSpan={showCreator ? 8 : 7} className="py-8 text-center text-fg-muted">No posts match.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
