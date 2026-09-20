import { useState } from "react";
import { Search } from "lucide-react";
import { usePosts, type PostFilters } from "../api/hooks/usePosts";
import { PageHeader } from "../components/layout/AppShell";
import { Pager, Spinner } from "../components/ui/Misc";
import { PostTable } from "../components/posts/PostTable";

export function SearchPage() {
  const [filters, setFilters] = useState<PostFilters>({ sort: "-published_at" });
  const [term, setTerm] = useState("");
  const posts = usePosts(filters);
  return (
    <>
      <PageHeader title="Search posts" subtitle={filters.q ? `${posts.data?.total ?? 0} results` : "Search across every creator"} />
      <div className="mb-4 flex max-w-xl gap-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-fg-dim" />
          <input className="input pl-8" autoFocus placeholder="Search all posts by title…" value={term} onChange={(e) => setTerm(e.target.value)} onKeyDown={(e) => e.key === "Enter" && setFilters({ ...filters, q: term || undefined, page: 1 })} />
        </div>
      </div>
      {filters.q && (posts.isLoading ? <div className="flex justify-center py-10"><Spinner /></div> : <PostTable posts={posts.data?.items ?? []} showCreator />)}
      {filters.q && posts.data && <Pager page={posts.data.page} pageSize={posts.data.page_size} total={posts.data.total} onChange={(p) => setFilters({ ...filters, page: p })} />}
    </>
  );
}
