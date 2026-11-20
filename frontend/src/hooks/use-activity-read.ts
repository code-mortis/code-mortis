import { useEffect, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type Schema } from "../api";

export const activityQueryKey = ["activity"] as const;
export const activityFeedQueryKey = [...activityQueryKey, "feed"] as const;

export function useActivityRead(
  kind: Schema["ActivityItem"]["kind"],
  id: string | undefined,
  finishedAt: string | null | undefined,
  loadedAt: number,
) {
  const cache = useQueryClient();
  const marked = useRef<string>(undefined);
  const { mutate } = useMutation({
    mutationFn: (result: { kind: Schema["ActivityItem"]["kind"]; id: string; finished_at: string }) =>
      api("/activity/read", "POST", { mode: "one", ...result }),
    retry: (count, error) => count < 2 && error instanceof ApiError && (error.status === 0 || error.status >= 500),
    onSuccess: () => cache.invalidateQueries({ queryKey: activityQueryKey }),
  });
  useEffect(() => {
    if (!id || !finishedAt || !loadedAt) return;
    const key = JSON.stringify([kind, id, finishedAt]);
    if (marked.current === key) return;
    marked.current = key;
    mutate({ kind, id, finished_at: finishedAt }, {
      onError: () => { if (marked.current === key) marked.current = undefined; },
    });
  }, [finishedAt, id, kind, loadedAt, mutate]);
}
