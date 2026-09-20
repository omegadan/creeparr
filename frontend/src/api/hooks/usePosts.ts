import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, qs } from "../client";
import type { MediaItem, Page, Post, PostDetail } from "../types";

export interface PostFilters {
  creator_id?: number;
  status?: string;
  post_type?: string;
  q?: string;
  page?: number;
  page_size?: number;
  sort?: string;
}

export const usePosts = (filters: PostFilters) =>
  useQuery({
    queryKey: ["posts", filters],
    queryFn: () => get<Page<Post>>(`/posts${qs({ page_size: 50, ...filters })}`),
    placeholderData: keepPreviousData,
  });

export const usePost = (id: number | null) =>
  useQuery({ queryKey: ["post", id], queryFn: () => get<PostDetail>(`/posts/${id}`), enabled: id !== null });

function usePostAction(action: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body?: unknown }) => post<PostDetail>(`/posts/${id}/${action}`, body),
    onSuccess: (data) => {
      qc.setQueryData(["post", data.id], data);
      qc.invalidateQueries({ queryKey: ["posts"] });
      qc.invalidateQueries({ queryKey: ["queue"] });
      qc.invalidateQueries({ queryKey: ["creators"] });
    },
  });
}

export const useDownloadPost = () => usePostAction("download");
export const useSkipPost = () => usePostAction("skip");
export const useUnskipPost = () => usePostAction("unskip");
export const useRefreshPost = () => usePostAction("refresh");

function useMediaAction(action: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => post<MediaItem>(`/media/${id}/${action}`),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["post", data.post_id] });
      qc.invalidateQueries({ queryKey: ["posts"] });
      qc.invalidateQueries({ queryKey: ["queue"] });
    },
  });
}

export const useRetryMedia = () => useMediaAction("retry");
export const useSkipMedia = () => useMediaAction("skip");
export const useUnskipMedia = () => useMediaAction("unskip");
