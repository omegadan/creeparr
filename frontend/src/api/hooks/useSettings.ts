import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, post, put } from "../client";
import type { NamingPreviewOut, NotificationTestOut, OkOut, SetAuthOut } from "../schema";
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
    mutationFn: (body: Creds) => put<SetAuthOut>(`/settings/auth/${provider}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["system"] });
      qc.invalidateQueries({ queryKey: ["providers"] });
    },
  });
}

export const useTestAuth = (provider: string) =>
  useMutation({ mutationFn: (body?: Creds) => post<AuthTestResult>(`/settings/auth/${provider}/test`, body) });

export function useClearAuth(provider: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => del<OkOut>(`/settings/auth/${provider}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["system"] });
      qc.invalidateQueries({ queryKey: ["providers"] });
    },
  });
}

export const useTestNotifications = () =>
  useMutation({ mutationFn: () => post<NotificationTestOut>("/settings/notifications/test") });

export const useNamingPreview = () =>
  useMutation({
    mutationFn: (body: { post_folder_template: string; file_template: string }) =>
      post<NamingPreviewOut>("/settings/naming-preview", body),
  });
