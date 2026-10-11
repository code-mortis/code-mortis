import { api, ApiError } from "../api";
import { INBOX_MAX_PAGES } from "./inbox-filters";

type GroupedResponse = {
  page_size: number;
  groups: { group: string; items: { id: string }[]; total: number; page: number; has_more: boolean }[];
};

export type GroupedData<T> = T & { errors: Partial<Record<string, string>> };

export async function fetchGroupedPages<T extends GroupedResponse>(path: string, filters: URLSearchParams, requested: Partial<Record<string, number>>, signal: AbortSignal): Promise<GroupedData<T>> {
  const first = await api<T>(`${path}?${filters}`, "GET", undefined, undefined, signal);
  const errors: Partial<Record<string, string>> = {};
  await Promise.all(first.groups.map(async (group) => {
    const wanted = Math.min(requested[group.group] ?? 1, Math.max(1, Math.ceil(group.total / first.page_size)), INBOX_MAX_PAGES);
    for (let page = 2; page <= wanted; page++) {
      signal.throwIfAborted();
      try {
        const params = new URLSearchParams(filters);
        params.set("group", group.group);
        params.set("page", String(page));
        const response = await api<T>(`${path}?${params}`, "GET", undefined, undefined, signal);
        const next = response.groups.find((item) => item.group === group.group);
        if (!next) break;
        group.items.push(...next.items);
        group.page = next.page;
        group.total = next.total;
        group.has_more = next.has_more;
        if (next.page < page || !next.has_more) break;
      } catch (error) {
        signal.throwIfAborted();
        if (error instanceof ApiError && [401, 403].includes(error.status)) throw error;
        errors[group.group] = error instanceof Error ? error.message : "Could not load more results.";
        break;
      }
    }
  }));
  const seen = new Set<string>();
  // Keep server totals and cursors when rows move between page requests.
  for (const group of first.groups) group.items = group.items.filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
  return { ...first, errors };
}
