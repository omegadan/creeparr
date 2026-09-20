import { useMemo, useState } from "react";
import { Plus, RefreshCw, Users, Import } from "lucide-react";
import { useCreators, useScanAll } from "../api/hooks/useCreators";
import { PageHeader } from "../components/layout/AppShell";
import { Button } from "../components/ui/Button";
import { EmptyState, Spinner } from "../components/ui/Misc";
import { CreatorCard } from "../components/creators/CreatorCard";
import { AddCreatorModal } from "../components/creators/AddCreatorModal";
import { ImportSubscriptionsModal } from "../components/creators/ImportSubscriptionsModal";
import { useToast } from "../components/ui/Toast";

export function CreatorsPage() {
  const creators = useCreators();
  const scanAll = useScanAll();
  const [add, setAdd] = useState(false);
  const [imp, setImp] = useState(false);
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState<"name" | "recent" | "progress">("name");
  const { toast, error } = useToast();

  const list = useMemo(() => {
    const items = (creators.data ?? []).filter((c) => c.name.toLowerCase().includes(filter.toLowerCase()));
    return items.sort((a, b) => {
      if (sort === "recent") return (b.last_scan_at ?? "").localeCompare(a.last_scan_at ?? "");
      if (sort === "progress") return (a.stats.media_completed / (a.stats.media_total || 1)) - (b.stats.media_completed / (b.stats.media_total || 1));
      return a.name.localeCompare(b.name);
    });
  }, [creators.data, filter, sort]);

  return (
    <>
      <PageHeader
        title="Creators"
        subtitle={creators.data ? `${creators.data.length} creator${creators.data.length === 1 ? "" : "s"} archived` : undefined}
        actions={
          <>
            <input className="input w-48" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <select className="input w-36" value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
              <option value="name">Sort: name</option>
              <option value="recent">Sort: last scan</option>
              <option value="progress">Sort: progress</option>
            </select>
            <Button icon={<RefreshCw className="h-4 w-4" />} loading={scanAll.isPending} onClick={() => scanAll.mutate(undefined, { onSuccess: (d) => toast(`Queued ${d.queued} scans`), onError: (e) => error(e) })}>
              Scan all
            </Button>
            <Button icon={<Import className="h-4 w-4" />} onClick={() => setImp(true)}>Import subscriptions</Button>
            <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setAdd(true)}>Add creator</Button>
          </>
        }
      />
      {creators.isLoading ? (
        <div className="flex justify-center py-20"><Spinner /></div>
      ) : creators.isError ? (
        <EmptyState title="Could not load creators" hint={(creators.error as Error).message} />
      ) : list.length === 0 ? (
        <EmptyState
          icon={<Users className="h-10 w-10" />}
          title={filter ? "No creators match" : "No creators yet"}
          hint={filter ? undefined : "Import the creators you pledge to, or add one by URL. Patrearr will back-fill their posts and keep checking for new ones."}
          action={
            !filter && (
              <div className="flex gap-2">
                <Button onClick={() => setImp(true)}>Import subscriptions</Button>
                <Button variant="primary" onClick={() => setAdd(true)}>Add creator</Button>
              </div>
            )
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
          {list.map((c) => <CreatorCard key={c.id} creator={c} />)}
        </div>
      )}
      <AddCreatorModal open={add} onClose={() => setAdd(false)} />
      <ImportSubscriptionsModal open={imp} onClose={() => setImp(false)} />
    </>
  );
}
