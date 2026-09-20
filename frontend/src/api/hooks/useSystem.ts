import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, qs } from "../client";
import type { AuthStatus, LogLine, SystemStatus, TaskInfo } from "../types";

export const useSystemStatus = (refetchInterval = 15_000) =>
  useQuery({ queryKey: ["system", "status"], queryFn: () => get<SystemStatus>("/system/status"), refetchInterval });

export function useAuthStatus(): AuthStatus | undefined {
  const status = useSystemStatus();
  const live = useQuery<AuthStatus>({ queryKey: ["auth"], queryFn: async () => (await get<SystemStatus>("/system/status")).auth, staleTime: Infinity });
  return live.data ?? status.data?.auth;
}

export const useTasks = () => useQuery({ queryKey: ["system", "tasks"], queryFn: () => get<TaskInfo[]>("/system/tasks"), refetchInterval: 10_000 });

export function useRunTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => post<{ started: boolean }>(`/system/tasks/${name}/run`),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["system", "tasks"] }), 1000),
  });
}

export const useLogs = (level: string, search: string, auto: boolean) =>
  useQuery({
    queryKey: ["system", "logs", level, search],
    queryFn: () => get<{ lines: LogLine[] }>(`/system/logs${qs({ lines: 1000, level, search })}`),
    refetchInterval: auto ? 3000 : false,
  });
