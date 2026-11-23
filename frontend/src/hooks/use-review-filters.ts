import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate, useNavigationType, useSearchParams } from "react-router-dom";
import { normalizeReviewFilters, reviewFilterKeys, reviewFilterPrefixes, withReviewFilters, type ReviewFilterKey, type ReviewFilterScope } from "../lib/review-filters";

export function useReviewFilters(scope: ReviewFilterScope, { enabled = true, legacyTab }: {
  enabled?: boolean; legacyTab?: "findings";
} = {}) {
  const [raw, setParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const navigationType = useNavigationType();
  const [navigation, setNavigation] = useState({ key: location.key, reset: 0 });
  if (navigation.key !== location.key) setNavigation({ key: location.key, reset: navigation.reset + (navigationType === "POP" ? 1 : 0) });
  const params = normalizeReviewFilters(raw, scope);
  const canonical = params.toString();
  const rawQuery = raw.toString();
  const current = new URLSearchParams();
  for (const key of reviewFilterKeys[scope]) for (const value of raw.getAll(`${reviewFilterPrefixes[scope]}${key}`)) current.append(key, value);
  const normalized = withReviewFilters(raw, params, scope);
  const needsTab = legacyTab && !normalized.has("tab");
  if (needsTab) normalized.set("tab", legacyTab);
  const needsNormalization = current.toString() !== canonical || needsTab;
  const normalizedQuery = normalized.toString();
  useEffect(() => {
    if (enabled && needsNormalization) setParams(normalizedQuery, { replace: true, state: location.state });
  }, [enabled, needsNormalization, normalizedQuery, setParams, location.state]);
  function resetPages(params: URLSearchParams) {
    for (const status of scope === "pull" || scope === "run" ? ["open"] : scope === "resolved" || scope === "dismissed" ? [scope] : []) params.delete(`${status}_page`);
    return params;
  }
  const search = useCallback((value: string) => {
    const next = new URLSearchParams(rawQuery);
    const key = `${reviewFilterPrefixes[scope]}q`;
    if (value.trim()) next.set(key, value); else next.delete(key);
    navigate({ search: resetPages(withReviewFilters(next, normalizeReviewFilters(next, scope), scope)).toString() },
      { replace: true, state: location.state });
  }, [rawQuery, scope, navigate, location.state]);
  function change(key: ReviewFilterKey, values: string[]) {
    const next = new URLSearchParams(raw);
    const name = `${reviewFilterPrefixes[scope]}${key}`;
    next.delete(name);
    if (key === "status" && !values.length) next.set(name, "all");
    else values.forEach((value) => next.append(name, value));
    setParams(resetPages(withReviewFilters(next, normalizeReviewFilters(next, scope), scope)), { state: location.state });
  }
  function clear() {
    const next = new URLSearchParams(scope === "pull" ? "status=open" : "");
    if (params.has("ordering")) next.set("ordering", params.get("ordering")!);
    setParams(resetPages(withReviewFilters(raw, next, scope)), { state: location.state });
    setNavigation((current) => ({ ...current, reset: current.reset + 1 }));
  }
  return { params, canonical, search, change, clear, searchResetKey: navigation.reset };
}
