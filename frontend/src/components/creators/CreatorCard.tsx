import { Link } from "react-router";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import type { Creator } from "../../api/types";
import { usePatchCreator } from "../../api/hooks/useCreators";
import { formatBytes, pct, timeAgo, cx } from "../../lib/format";
import { ProgressBar } from "../ui/ProgressBar";
import { Avatar } from "../ui/Misc";
import { Badge } from "../ui/Badge";

const PROVIDER_LABEL: Record<string, string> = { patreon: "Patreon", onlyfans: "OnlyFans", youtube: "YouTube", instagram: "Instagram", reddit: "Reddit" };

export function CreatorCard({ creator }: { creator: Creator }) {
  const patch = usePatchCreator(creator.id);
  const s = creator.stats;
  const done = pct(s.media_completed, s.media_total);
  return (
    <div className={cx("card group relative flex flex-col overflow-hidden transition-colors hover:border-fg-dim", !creator.monitored && "opacity-70")}>
      <Link to={`/creators/${creator.id}`} className="flex items-center gap-3 p-4">
        <Avatar src={creator.avatar_url} name={creator.name} size={48} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5"><span className="truncate font-semibold">{creator.name}</span>{creator.provider !== "patreon" && <Badge tone="muted">{PROVIDER_LABEL[creator.provider] ?? creator.provider}</Badge>}</div>
          <div className="truncate text-xs text-fg-muted">{creator.vanity ? `@${creator.vanity}` : creator.campaign_id}</div>
        </div>
      </Link>
      <div className="px-4 pb-3">
        <div className="mb-1 flex items-center justify-between text-xs text-fg-muted">
          <span>
            {s.media_completed}/{s.media_total} files
          </span>
          <span>{done}%</span>
        </div>
        <ProgressBar value={done} tone={done === 100 && s.media_total > 0 ? "ok" : "accent"} />
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-fg-dim">
          <span>{s.posts_total} posts</span>
          {s.bytes > 0 && <span>{formatBytes(s.bytes)}</span>}
          {s.posts_pending > 0 && <Badge tone="info">{s.posts_pending} pending</Badge>}
          {s.posts_no_access > 0 && <Badge tone="muted">{s.posts_no_access} locked</Badge>}
          {s.posts_unsupported > 0 && <Badge tone="danger">{s.posts_unsupported} DRM</Badge>}
          {creator.last_scan_status === "error" && <Badge tone="danger" title={creator.last_scan_error ?? ""}>scan error</Badge>}
        </div>
      </div>
      <div className="mt-auto flex items-center justify-between border-t border-line px-4 py-2 text-[11px] text-fg-dim">
        <span className="flex items-center gap-1">
          {creator.scanning && <Loader2 className="h-3 w-3 animate-spin text-info" />}
          {creator.scanning ? "scanning…" : `scanned ${timeAgo(creator.last_scan_at)}`}
        </span>
        <button
          title={creator.monitored ? "Monitored: click to stop monitoring" : "Not monitored: click to monitor"}
          onClick={() => patch.mutate({ monitored: !creator.monitored })}
          className="rounded p-1 text-fg-muted hover:bg-bg-3 hover:text-fg"
        >
          {creator.monitored ? <Eye className="h-4 w-4 text-ok" /> : <EyeOff className="h-4 w-4" />}
        </button>
      </div>
    </div>
  );
}
