import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { get, qs } from "../client";
import type { HistoryEvent, Page } from "../types";

export interface HistoryFilters {
  page?: number;
  page_size?: number;
  event_type?: string;
  creator_id?: number;
  level?: string;
}

export const useHistory = (filters: HistoryFilters) =>
  useQuery({
    queryKey: ["history", filters],
    queryFn: () => get<Page<HistoryEvent>>(`/history${qs({ page_size: 50, ...filters })}`),
    placeholderData: keepPreviousData,
  });
