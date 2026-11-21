import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Schema } from "../api";
import { activityFeedQueryKey } from "./use-activity-read";

export function useRefreshQueuedReviews(groups: Schema["InboxOut"]["groups"] | undefined) {
  const cache = useQueryClient();
  const seen = useRef(new Set<string>());
  useEffect(() => {
    let refresh = false;
    for (const group of groups ?? []) {
      for (const pr of group.items) {
        if (pr.review_status !== "queued" || !pr.run_id || seen.current.has(pr.run_id)) continue;
        seen.current.add(pr.run_id);
        refresh = true;
      }
    }
    if (!refresh) return;
    const feed = { queryKey: activityFeedQueryKey };
    // An idle feed request might have started before the review was queued.
    void cache.cancelQueries(feed).then(() => cache.invalidateQueries(feed));
  }, [cache, groups]);
}
