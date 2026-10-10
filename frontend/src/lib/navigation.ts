export function getDetailPath(pathname: string) {
  const path = pathname.replace(/\/+$/, "");
  return /^\/(pulls|runs)\/[^/]+$/.test(path) ? path : null;
}

export function getRepositoryReturn(state: unknown): string | null {
  if (!state || typeof state !== "object" || !("repository" in state)) return null;
  return typeof state.repository === "string" && /^\/repositories\/[1-9]\d*(?:\?[^#]*)?$/.test(state.repository) ? state.repository : null;
}
