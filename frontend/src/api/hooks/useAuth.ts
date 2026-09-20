import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, post, put } from "../client";

export interface AuthState {
  auth_enabled: boolean;
  authenticated: boolean;
}

export const useAuthState = () =>
  useQuery({ queryKey: ["authState"], queryFn: () => get<AuthState>("/auth/status"), staleTime: 5_000 });

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (password: string) => post<{ ok: boolean }>("/auth/login", { password }),
    onSuccess: () => qc.invalidateQueries(),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => post<{ ok: boolean }>("/auth/logout"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["authState"] }),
  });
}

export function useSetPassword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { password: string; current_password?: string }) => put<{ ok: boolean }>("/auth/password", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["authState"] }),
  });
}

export function useDisableAuth() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => del<{ ok: boolean }>("/auth/password"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["authState"] }),
  });
}
