import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ArrowLeft, ExternalLink, RefreshCw, Settings2, Trash2 } from "lucide-react";
import { useCreator, useDeleteCreator, usePatchCreator, useRefreshCreator, useScanCreator, useScanRuns } from "../api/hooks/useCreators";
import { usePosts, type PostFilters as Filters } from "../api/hooks/usePosts";
import type { CreatorDefaults } from "../api/types";
import { PageHeader } from "../components/layout/AppShell";
import { Button } from "../components/ui/Button";
import { Avatar, EmptyState, Pager, Spinner } from "../components/ui/Misc";
import { Modal, ConfirmDialog } from "../components/ui/Modal";
import { Badge } from "../components/ui/Badge";
import { ProgressBar } from "../components/ui/ProgressBar";
import { CreatorOptions } from "../components/creators/CreatorOptions";
import { PostFilters } from "../components/posts/PostFilters";
import { PostTable } from "../components/posts/PostTable";
import { useToast } from "../components/ui/Toast";
import { formatDate, pct, timeAgo } from "../lib/format";

export function CreatorDetailPage() {
  const id = Number(useParams().id);
  const creator = useCreator(id);
  const [filters, setFilters] = useState<Filters>({ sort: "-published_at" });
  const posts = usePosts({ ...filters, creator_id: id });
  const runs = useScanRuns(id);
  const scan = useScanCreator();
  const patch = usePatchCreator(id);
  const refresh = useRefreshCreator(id);
  const remove = useDeleteCreator();
  const nav = useNavigate();
  const { toast, error } = useToast();
  const [editing, setEditing] = useState<CreatorDefaults | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteFiles, setDeleteFiles] = useState(false);

  if (creator.isLoading) return <div className="flex justify-center py-20"><Spinner /></div>;
  if (creator.isError || !creator.data) return <EmptyState title="Creator not found" action={<Link to="/creators"><Button>Back to creators</Button></Link>} />;
  const c = creator.data;
  const s = c.stats;
  const done = pct(s.media_completed, s.media_total);

  const doScan = (mode: "auto" | "full") =>
    scan.mutate({ id, mode }, { onSuccess: (d) => toast(d.queued ? `${mode === "full" ? "Full" : "Incremental"} scan queued` : "A scan is already pending"), onError: (e) => error(e) });

  return (
    <>
      <Link to="/creators" className="mb-3 inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg"><ArrowLeft className="h-3.5 w-3.5" /> Creators</Link>
      <div className="card mb-5 overflow-hidden">
        {c.cover_url && <div className="h-28 bg-cover bg-center opacity-60" style={{ backgroundImage: `url(${c.cover_url})` }} />}
        <div className="flex flex-wrap items-start gap-4 p-5">
          <Avatar src={c.avatar_url} name={c.name} size={72} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-bold">{c.name}</h1>
              {!c.monitored && <Badge tone="muted">not monitored</Badge>}
              {!c.auto_download && <Badge tone="warn">manual download</Badge>}
              {c.is_nsfw && <Badge tone="danger">18+</Badge>}
              {c.scanning && <Badge tone="info">scanning…</Badge>}
            </div>
            <div className="mt-0.5 flex items-center gap-2 text-sm text-fg-muted">
              <span>{c.vanity ? `@${c.vanity}` : `campaign ${c.campaign_id}`}</span>
              {c.url && <a href={c.url} target="_blank" rel="noreferrer" className="hover:text-fg"><ExternalLink className="h-3.5 w-3.5" /></a>}
            </div>
            <div className="mt-3 max-w-lg">
              <div className="mb-1 flex justify-between text-xs text-fg-muted"><span>{s.media_completed} of {s.media_total} files archived</span><span>{done}%</span></div>
              <ProgressBar value={done} tone={done === 100 && s.media_total > 0 ? "ok" : "accent"} />
            </div>
            <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-fg-muted">
              <span>{s.posts_total} posts</span>
              <span className="text-ok">{s.posts_completed} archived</span>
              <span className="text-info">{s.posts_pending} pending</span>
              {s.posts_no_access > 0 && <span>{s.posts_no_access} locked</span>}
              {s.posts_unsupported > 0 && <span className="text-danger">{s.posts_unsupported} DRM/unsupported</span>}
              <span>last scan {timeAgo(c.last_scan_at)}{c.last_scan_status === "error" ? ` (error: ${c.last_scan_error})` : ""}</span>
              <span className="flex gap-1">archiving: video{c.include_images && ", images"}{c.include_audio && ", audio"}{c.include_attachments && ", attachments"}</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" icon={<RefreshCw className="h-3.5 w-3.5" />} onClick={() => doScan("auto")} loading={scan.isPending}>Scan now</Button>
            <Button size="sm" onClick={() => doScan("full")}>Full rescan</Button>
            <Button size="sm" icon={<Settings2 className="h-3.5 w-3.5" />} onClick={() => setEditing({ monitored: c.monitored, auto_download: c.auto_download, include_images: c.include_images, include_audio: c.include_audio, include_attachments: c.include_attachments, download_since: c.download_since, folder_name: c.folder_name })}>
              Options
            </Button>
            <Button size="sm" variant="danger" icon={<Trash2 className="h-3.5 w-3.5" />} onClick={() => setConfirmDelete(true)}>Remove</Button>
          </div>
        </div>
      </div>

      <PageHeader title="Posts" subtitle={posts.data ? `${posts.data.total} posts` : undefined} />
      <PostFilters value={filters} onChange={setFilters} />
      {posts.isLoading ? <div className="flex justify-center py-10"><Spinner /></div> : <PostTable posts={posts.data?.items ?? []} />}
      {posts.data && <Pager page={posts.data.page} pageSize={posts.data.page_size} total={posts.data.total} onChange={(p) => setFilters({ ...filters, page: p })} />}

      {runs.data && runs.data.length > 0 && (
        <details className="mt-6">
          <summary className="cursor-pointer text-sm text-fg-muted hover:text-fg">Recent scans</summary>
          <div className="card mt-2 overflow-x-auto">
            <table className="table text-xs">
              <thead><tr><th>Started</th><th>Mode</th><th>Trigger</th><th>Status</th><th>Pages</th><th>Seen</th><th>New</th><th>Updated</th><th>Queued</th><th>Error</th></tr></thead>
              <tbody>
                {runs.data.map((r) => (
                  <tr key={r.id}>
                    <td>{formatDate(r.started_at, true)}</td><td>{r.mode}</td><td>{r.trigger}</td>
                    <td><Badge tone={r.status === "ok" ? "ok" : r.status === "running" ? "info" : r.status === "error" ? "danger" : "muted"}>{r.status}</Badge></td>
                    <td>{r.pages_fetched}</td><td>{r.posts_seen}</td><td>{r.posts_new}</td><td>{r.posts_updated}</td><td>{r.media_queued}</td>
                    <td className="max-w-xs truncate text-fg-muted" title={r.error ?? ""}>{r.error}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      <Modal
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={`Options for ${c.name}`}
        footer={
          <>
            <Button variant="ghost" onClick={() => refresh.mutate(undefined, { onSuccess: () => toast("Metadata refreshed", "success"), onError: (e) => error(e) })} loading={refresh.isPending}>Refresh metadata</Button>
            <Button variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
            <Button variant="primary" loading={patch.isPending} onClick={() => editing && patch.mutate(editing, { onSuccess: () => { toast("Saved", "success"); setEditing(null); }, onError: (e) => error(e) })}>Save</Button>
          </>
        }
      >
        {editing && <CreatorOptions value={editing} onChange={setEditing} showFolder />}
      </Modal>

      <ConfirmDialog
        open={confirmDelete}
        title={`Remove ${c.name}?`}
        danger
        confirmLabel="Remove"
        loading={remove.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate({ id, deleteFiles }, { onSuccess: () => { toast(`Removed ${c.name}`); nav("/creators"); }, onError: (e) => error(e) })}
        message={
          <div className="grid gap-3">
            <p>This removes the creator and all its post records from Patrearr. Running downloads are cancelled.</p>
            <label className="flex items-center gap-2 text-fg"><input type="checkbox" checked={deleteFiles} onChange={(e) => setDeleteFiles(e.target.checked)} /> Also delete archived files from disk</label>
          </div>
        }
      />
    </>
  );
}
