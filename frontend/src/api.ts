import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "./api.generated";

export type Schema = components["schemas"];
export type Repository = Schema["RepositoryOut"];
export type Run = Schema["RunOut"];
export type Finding = Schema["FindingOut"];
export type Connection = Schema["ConnectionOut"];
let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  headers?: HeadersInit,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method,
    signal,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
      ...headers,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  }).catch((error: unknown) => {
    if (signal?.aborted || (error instanceof Error && error.name === "AbortError")) throw error;
    throw new ApiError(0, "Could not reach Code Mortis. Check your connection and try again. If it keeps failing, ask the administrator to check the server.");
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const messages: Record<number, string> = {
      401: "Your session has expired. Sign in with GitHub again.",
      403: "You do not have access to this action. Ask the administrator to check your permissions.",
      404: "This item could not be found. Refresh the page to check whether it is still available.",
      409: "This item changed while you were working. Refresh the page and try again.",
      413: "This request is too large. Reduce the amount of data and try again.",
      422: "Some values could not be accepted. Check the form fields and try again.",
      429: "Too many requests were sent. Wait a moment, then try again.",
      502: "Code Mortis could not reach a service it needs. Try again shortly.",
      503: "Code Mortis is temporarily unavailable. Try again shortly.",
      504: "The server took too long to respond. Check whether the action completed before trying again.",
    };
    throw new ApiError(
      response.status,
      typeof data?.detail === "string" && data.detail.trim()
        ? data.detail
        : messages[response.status] ?? "Code Mortis could not complete this request. Try again. If it keeps failing, ask the administrator to check the server logs.",
    );
  }
  return data as T;
}
export function useApi<T>(path: string, poll = false, enabled = true) {
  return useQuery<T, ApiError>({
    queryKey: [path],
    queryFn: () => api<T>(path),
    enabled,
    refetchInterval: poll ? 5000 : false,
    retry: false,
  });
}
export function useAction<T = unknown>() {
  const cache = useQueryClient();
  return useMutation<
    T,
    Error,
    { path: string; method?: string; body?: unknown }
  >({
    mutationFn: ({ path, method = "POST", body }) => api<T>(path, method, body),
    onSuccess: () => {
      void cache.invalidateQueries();
    },
  });
}
