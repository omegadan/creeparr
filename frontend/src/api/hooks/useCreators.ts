import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, patch, post, qs } from "../client";
import type { CampaignPreview, Creator, CreatorDefaults, Pledge, ScanRun } from "../types";

export const useCreators = () => useQuery({ queryKey: ["creators"], queryFn: () => get<Creator[]>("/creators") });
export const useCreator = (id: number) =>
  useQuery({ queryKey: ["creator", id], queryFn: () => get<Creator>(`/creators/${id}`), enabled: Number.isFinite(id) });
export const useScanRuns = (id: number) =>
  useQuery({ queryKey: ["scans", id], queryFn: () => get<ScanRun[]>(`/creators/${id}/scans?limit=10`), enabled: Number.isFinite(id) });
export const usePledges = (enabled: boolean) =>
  useQuery({ queryKey: ["pledges"], queryFn: () => get<Pledge[]>("/patreon/pledges"), enabled, staleTime: 60_000 });

export function useLookupCreator() {
  return useMutation({ mutationFn: (query: string) => post<CampaignPreview>("/creators/lookup", { query }) });
}

export function useAddCreator() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreatorDefaults & { query: string }) => post<Creator>("/creators", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["creators"] });
      qc.invalidateQueries({ queryKey: ["pledges"] });
    },
  });
}

export function useImportPledges() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { campaign_ids: string[]; defaults: CreatorDefaults }) => post<Creator[]>("/creators/import-pledges", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["creators"] });
      qc.invalidateQueries({ queryKey: ["pledges"] });
    },
  });
}

export function usePatchCreator(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreatorDefaults) => patch<Creator>(`/creators/${id}`, body),
    onSuccess: (data) => {
      qc.setQueryData(["creator", id], data);
      qc.invalidateQueries({ queryKey: ["creators"] });
      qc.invalidateQueries({ queryKey: ["posts"] });
    },
  });
}

export function useDeleteCreator() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, deleteFiles }: { id: number; deleteFiles: boolean }) => del<void>(`/creators/${id}${qs({ delete_files: deleteFiles })}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["creators"] }),
  });
}

export function useScanCreator() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, mode }: { id: number; mode: "auto" | "full" | "incremental" }) => post<{ queued: boolean }>(`/creators/${id}/scan`, { mode }),
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ["creators"] });
      qc.invalidateQueries({ queryKey: ["creator", v.id] });
    },
  });
}

export function useScanAll() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => post<{ queued: number }>("/creators/scan-all"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["creators"] }),
  });
}

export function useRefreshCreator(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => post<Creator>(`/creators/${id}/refresh-metadata`),
    onSuccess: (data) => {
      qc.setQueryData(["creator", id], data);
      qc.invalidateQueries({ queryKey: ["creators"] });
    },
  });
}
