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
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
      ...headers,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok)
    throw new ApiError(
      response.status,
      typeof data?.detail === "string"
        ? data.detail
        : "Something went wrong. Please retry.",
    );
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
