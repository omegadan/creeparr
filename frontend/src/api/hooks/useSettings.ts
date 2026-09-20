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

export function useSetPatreonAuth() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { session_id?: string; cookies_txt?: string }) => put<AuthTestResult & { settings: Settings["patreon"] }>("/settings/patreon-auth", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["system"] });
      qc.invalidateQueries({ queryKey: ["auth"] });
    },
  });
}

export const useTestPatreonAuth = () =>
  useMutation({ mutationFn: (body?: { session_id?: string; cookies_txt?: string }) => post<AuthTestResult>("/settings/patreon-auth/test", body) });

export function useClearPatreonAuth() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => del<{ ok: boolean }>("/settings/patreon-auth"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["system"] });
      qc.invalidateQueries({ queryKey: ["auth"] });
    },
  });
}

export const useNamingPreview = () =>
  useMutation({
    mutationFn: (body: { post_folder_template: string; file_template: string }) =>
      post<{ post_folder: string; file: string; full_path: string }>("/settings/naming-preview", body),
  });
