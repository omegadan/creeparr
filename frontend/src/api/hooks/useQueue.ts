import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, post, qs } from "../client";
import type { Queue } from "../types";

export const useQueue = (page = 1, failedPage = 1) =>
  useQuery({
    queryKey: ["queue", page, failedPage],
    queryFn: () => get<Queue>(`/queue${qs({ page, failed_page: failedPage })}`),
    refetchInterval: 10_000,
    placeholderData: keepPreviousData,
  });

function useQueueMutation<TArgs>(fn: (args: TArgs) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["queue"] });
      qc.invalidateQueries({ queryKey: ["posts"] });
      qc.invalidateQueries({ queryKey: ["system", "status"] });
    },
  });
}

export const usePauseQueue = () => useQueueMutation(() => post("/queue/pause"));
export const useResumeQueue = () => useQueueMutation(() => post("/queue/resume"));
export const useRemoveJob = () => useQueueMutation(({ id, skip }: { id: number; skip: boolean }) => del(`/queue/${id}${qs({ skip_media: skip })}`));
export const useRetryJob = () => useQueueMutation((id: number) => post(`/queue/${id}/retry`));
export const useRetryFailed = () => useQueueMutation(() => post("/queue/retry-failed"));
export const useClearFailed = () => useQueueMutation(() => del("/queue/failed"));
