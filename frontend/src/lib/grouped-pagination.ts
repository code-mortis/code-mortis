import { api, ApiError } from "../api";
import { INBOX_MAX_PAGES } from "./inbox-filters";

type GroupedResponse = {
  page_size: number;
  groups: { group: string; items: { id: string }[]; total: number; page: number; has_more: boolean }[];
};

export type GroupedData<T> = T & { errors: Partial<Record<string, string>> };

export async function fetchGroupedPages<T extends GroupedResponse>(path: string, filters: URLSearchParams, requested: Partial<Record<string, number>>, signal: AbortSignal): Promise<GroupedData<T>> {
  const params = new URLSearchParams(filters);
  const selected = params.get("group");
  if (selected && (requested[selected] ?? 1) > 1) params.set("page", String(requested[selected]));
  const first = await api<T>(`${path}?${params}`, "GET", undefined, undefined, signal);
  const errors: Partial<Record<string, string>> = {};
  await Promise.all(first.groups.map(async (group) => {
    const wanted = Math.min(requested[group.group] ?? 1, Math.max(1, Math.ceil(group.total / first.page_size)), INBOX_MAX_PAGES);
    if (!selected && wanted > 1) {
      signal.throwIfAborted();
      try {
        const params = new URLSearchParams(filters);
        params.set("group", group.group);
        params.set("page", String(wanted));
        const response = await api<T>(`${path}?${params}`, "GET", undefined, undefined, signal);
        const next = response.groups.find((item) => item.group === group.group);
        if (next) Object.assign(group, next);
      } catch (error) {
        signal.throwIfAborted();
        if (error instanceof ApiError && [401, 403].includes(error.status)) throw error;
        errors[group.group] = error instanceof Error ? error.message : "Could not load this page.";
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
