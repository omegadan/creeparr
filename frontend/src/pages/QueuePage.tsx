import { Pause, Play, RotateCcw, Trash2 } from "lucide-react";
import { useClearFailed, usePauseQueue, useQueue, useResumeQueue, useRetryFailed } from "../api/hooks/useQueue";
import { PageHeader } from "../components/layout/AppShell";
import { Button } from "../components/ui/Button";
import { Spinner } from "../components/ui/Misc";
import { FailedTable, JobTable } from "../components/queue/QueueTable";
import { useToast } from "../components/ui/Toast";

export function QueuePage() {
  const queue = useQueue();
  const pause = usePauseQueue();
  const resume = useResumeQueue();
  const retryFailed = useRetryFailed();
  const clearFailed = useClearFailed();
  const { toast, error } = useToast();
  const q = queue.data;
  return (
    <>
      <PageHeader
        title="Queue"
        subtitle={q ? `${q.jobs.filter((j) => j.status === "running").length} downloading · ${q.jobs.filter((j) => j.status === "queued").length} queued · ${q.failed.length} failed` : undefined}
        actions={
          <>
            {q?.paused ? (
              <Button variant="primary" icon={<Play className="h-4 w-4" />} loading={resume.isPending} onClick={() => resume.mutate(undefined, { onError: (e) => error(e) })}>Resume</Button>
            ) : (
              <Button icon={<Pause className="h-4 w-4" />} loading={pause.isPending} onClick={() => pause.mutate(undefined, { onError: (e) => error(e) })}>Pause</Button>
            )}
            <Button icon={<RotateCcw className="h-4 w-4" />} loading={retryFailed.isPending} onClick={() => retryFailed.mutate(undefined, { onSuccess: (d) => toast(`Re-queued ${(d as { requeued: number }).requeued}`), onError: (e) => error(e) })}>Retry failed</Button>
            <Button variant="danger" icon={<Trash2 className="h-4 w-4" />} loading={clearFailed.isPending} onClick={() => clearFailed.mutate(undefined, { onSuccess: (d) => toast(`Cleared ${(d as { cleared: number }).cleared}`), onError: (e) => error(e) })}>Clear failed</Button>
          </>
        }
      />
      {queue.isLoading || !q ? (
        <div className="flex justify-center py-20"><Spinner /></div>
      ) : (
        <div className="grid gap-6">
          <JobTable jobs={q.jobs} />
          {q.failed.length > 0 && (
            <div>
              <h2 className="mb-2 text-sm font-semibold text-fg-muted">Failed</h2>
              <FailedTable items={q.failed} />
            </div>
          )}
        </div>
      )}
    </>
  );
}
