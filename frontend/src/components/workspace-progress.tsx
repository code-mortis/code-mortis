import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useIsFetching, useIsMutating } from "@tanstack/react-query";

const PendingFallbacks = createContext({ pending: 0, register: () => () => {} });

export function WorkspaceProgressProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState(0);
  const register = useCallback(() => {
    setPending((count) => count + 1);
    return () => setPending((count) => count - 1);
  }, []);
  return <PendingFallbacks.Provider value={{ pending, register }}>{children}</PendingFallbacks.Provider>;
}

export function useWorkspaceProgress(active: boolean) {
  const { register } = useContext(PendingFallbacks);
  useEffect(() => active ? register() : undefined, [active, register]);
}

export function WorkspaceProgressFallback() {
  useWorkspaceProgress(true);
  return null;
}

export function WorkspaceProgress() {
  const initialLoads = useIsFetching({ predicate: (query) => !query.isFetched() });
  const mutating = useIsMutating();
  const { pending } = useContext(PendingFallbacks);
  if (!initialLoads && !mutating && !pending) return null;
  return <div role="progressbar" aria-label="Loading workspace" className="workspace-progress pointer-events-none absolute inset-x-0 -bottom-px z-10 h-0.5 overflow-hidden bg-primary/20">
    <div className="h-full bg-primary" />
  </div>;
}
