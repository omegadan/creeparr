import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, post, put } from "../client";
import type { AuthTestResult, Settings, SettingsPatch } from "../types";

export const useSettings = () => useQuery({ queryKey: ["settings"], queryFn: () => get<Settings>("/settings") });

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: SettingsPatch) => put<Settings>("/settings", patch),
    onSuccess: (data) => {
      qc.setQueryData(["settings"], data);
      qc.invalidateQueries({ queryKey: ["system"] });
    },
  });
}

type Creds = Record<string, string>;

export function useSetAuth(provider: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Creds) => put<AuthTestResult & { settings: Record<string, unknown> }>(`/settings/auth/${provider}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["system"] });
      qc.invalidateQueries({ queryKey: ["providers"] });
      qc.invalidateQueries({ queryKey: ["auth"] });
    },
  });
}

export const useTestAuth = (provider: string) =>
  useMutation({ mutationFn: (body?: Creds) => post<AuthTestResult>(`/settings/auth/${provider}/test`, body) });

export function useClearAuth(provider: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => del<{ ok: boolean }>(`/settings/auth/${provider}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["system"] });
      qc.invalidateQueries({ queryKey: ["providers"] });
      qc.invalidateQueries({ queryKey: ["auth"] });
    },
  });
}

export const useNamingPreview = () =>
  useMutation({
    mutationFn: (body: { post_folder_template: string; file_template: string }) =>
      post<{ post_folder: string; file: string; full_path: string }>("/settings/naming-preview", body),
  });
