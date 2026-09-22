import { useState } from "react";
import { useHistory, type HistoryFilters } from "../api/hooks/useHistory";
import { PageHeader } from "../components/layout/AppShell";
import { Pager, Spinner } from "../components/ui/Misc";
import { HistoryTable } from "../components/history/HistoryTable";

const TYPES = ["download_completed", "download_failed", "scan_completed", "scan_failed", "creator_added", "creator_removed", "media_unsupported", "media_missing", "media_restored", "auth_invalid", "auth_valid"];

export function HistoryPage() {
  const [filters, setFilters] = useState<HistoryFilters>({});
  const history = useHistory(filters);
  return (
    <>
      <PageHeader
        title="History"
        actions={
          <>
            <select className="input w-48" value={filters.event_type ?? ""} onChange={(e) => setFilters({ ...filters, event_type: e.target.value || undefined, page: 1 })}>
              <option value="">All events</option>
              {TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
            </select>
            <select className="input w-36" value={filters.level ?? ""} onChange={(e) => setFilters({ ...filters, level: e.target.value || undefined, page: 1 })}>
              <option value="">All levels</option>
              <option value="info">Info</option>
              <option value="warning">Warning</option>
              <option value="error">Error</option>
            </select>
          </>
        }
      />
      {history.isLoading ? <div className="flex justify-center py-20"><Spinner /></div> : <HistoryTable events={history.data?.items ?? []} />}
      {history.data && <Pager page={history.data.page} pageSize={history.data.page_size} total={history.data.total} onChange={(p) => setFilters({ ...filters, page: p })} />}
    </>
  );
}
