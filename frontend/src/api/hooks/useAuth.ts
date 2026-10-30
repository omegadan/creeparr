import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, post, put } from "../client";
import type { LoginStateOut, OkOut } from "../schema";

export const useAuthState = () =>
  useQuery({ queryKey: ["authState"], queryFn: () => get<LoginStateOut>("/auth/status"), staleTime: 5_000 });

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (password: string) => post<OkOut>("/auth/login", { password }),
    onSuccess: () => qc.invalidateQueries(),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => post<OkOut>("/auth/logout"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["authState"] }),
  });
}

export function useSetPassword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { password: string; current_password?: string }) => put<OkOut>("/auth/password", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["authState"] }),
  });
}

export function useDisableAuth() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => del<OkOut>("/auth/password"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["authState"] }),
  });
}
