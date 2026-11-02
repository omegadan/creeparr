import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, patch, post, qs } from "../client";
import type { CreatorDefaults, CreatorPreview, Creator, ProviderInfo, ScanRun, Subscription } from "../types";
import type { ScanMode, ScanRequestedOut, ScansQueuedOut } from "../schema";
import { postTypeLabels } from "../../lib/providers";

export const useCreators = () => useQuery({ queryKey: ["creators"], queryFn: () => get<Creator[]>("/creators") });
export const useCreator = (id: number) =>
  useQuery({ queryKey: ["creator", id], queryFn: () => get<Creator>(`/creators/${id}`), enabled: Number.isFinite(id) });
export const useScanRuns = (id: number) =>
  useQuery({ queryKey: ["scans", id], queryFn: () => get<ScanRun[]>(`/creators/${id}/scans?limit=10`), enabled: Number.isFinite(id) });
export const useProviders = () =>
  useQuery({ queryKey: ["providers"], queryFn: () => get<ProviderInfo[]>("/providers") });

/** Every provider's post types with their labels. */
export function usePostTypeLabels(): Record<string, string> {
  const providers = useProviders();
  return useMemo(() => postTypeLabels(providers.data ?? []), [providers.data]);
}

export const useSubscriptions = (provider: string, enabled: boolean) =>
  useQuery({
    queryKey: ["subscriptions", provider],
    queryFn: () => get<Subscription[]>(`/providers/${provider}/subscriptions`),
    enabled,
    staleTime: 60_000,
  });

export function useLookupCreator() {
  return useMutation({
    mutationFn: ({ query, provider }: { query: string; provider: string }) =>
      post<CreatorPreview>("/creators/lookup", { query, provider }),
  });
}

export function useAddCreator() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreatorDefaults & { query: string; provider: string }) => post<Creator>("/creators", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["creators"] });
      qc.invalidateQueries({ queryKey: ["subscriptions"] });
    },
  });
}

export function useImportSubscriptions() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { provider: string; ids: string[]; defaults: CreatorDefaults }) =>
      post<Creator[]>("/creators/import-subscriptions", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["creators"] });
      qc.invalidateQueries({ queryKey: ["subscriptions"] });
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
    mutationFn: ({ id, mode }: { id: number; mode: ScanMode }) => post<ScanRequestedOut>(`/creators/${id}/scan`, { mode }),
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ["creators"] });
      qc.invalidateQueries({ queryKey: ["creator", v.id] });
    },
  });
}

export function useScanAll() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => post<ScansQueuedOut>("/creators/scan-all"),
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
