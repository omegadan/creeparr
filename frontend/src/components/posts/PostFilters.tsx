import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { useDebounced } from "../../lib/useDebounced";
import { POST_STATUS, POST_TYPES } from "../../lib/status";
import type { PostFilters as Filters } from "../../api/hooks/usePosts";

export function PostFilters({ value, onChange }: { value: Filters; onChange: (f: Filters) => void }) {
  const set = (k: keyof Filters, v: string | undefined) => onChange({ ...value, [k]: v || undefined, page: 1 });
  // Type freely; the filter (and the request) follows once typing pauses.
  const [term, setTerm] = useState(value.q ?? "");
  const settled = useDebounced(term);
  useEffect(() => {
    if ((settled || undefined) !== value.q) set("q", settled);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled]);
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-fg-dim" />
        <input className="input w-64 pl-8" aria-label="Search titles" placeholder="Search titles…" value={term} onChange={(e) => setTerm(e.target.value)} />
      </div>
      <select className="input w-40" value={value.status ?? ""} onChange={(e) => set("status", e.target.value)}>
        <option value="">All statuses</option>
        {Object.entries(POST_STATUS).map(([k, m]) => (
          <option key={k} value={k}>{m.label}</option>
        ))}
      </select>
      <select className="input w-36" value={value.post_type ?? ""} onChange={(e) => set("post_type", e.target.value)}>
        <option value="">All types</option>
        {Object.entries(POST_TYPES).map(([k, l]) => (
          <option key={k} value={k}>{l}</option>
        ))}
      </select>
      <select className="input w-40" value={value.sort ?? "-published_at"} onChange={(e) => set("sort", e.target.value)}>
        <option value="-published_at">Newest first</option>
        <option value="published_at">Oldest first</option>
        <option value="title">Title A–Z</option>
        <option value="status">Status</option>
      </select>
    </div>
  );
}
