import { useCallback, useLayoutEffect, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useBlocker } from "react-router-dom";
import { api } from "../api";
import { activityQueryKey } from "../hooks/use-activity-read";
import { ErrorNotice } from "./common";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./ui/dialog";

export function useContextGenerationGuard(repositoryId: number, generationId?: string, onCancelled?: (id: string) => void, summaryOnly = false, onStay?: () => void) {
  const handingOff = useRef(false);
  const previousFocus = useRef<HTMLElement | null>(null);
  const handOff = useCallback(() => { handingOff.current = true; }, []);
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    !!generationId && !handingOff.current
    && (currentLocation.pathname !== nextLocation.pathname
      || new URLSearchParams(currentLocation.search).get("tab") !== new URLSearchParams(nextLocation.search).get("tab")));
  const path = `/repositories/${repositoryId}/background/cancel`;
  const cache = useQueryClient();
  const cancel = useMutation({
    mutationFn: (id: string) => api(path, "POST", { generation_id: id }),
    onSuccess: () => { void cache.invalidateQueries({ queryKey: activityQueryKey }); },
  });

  useLayoutEffect(() => {
    if (!generationId) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    // pagehide runs only when the browser actually leaves the page.
    const leave = () => { void api(path, "POST", { generation_id: generationId }, undefined, undefined, true).catch(() => {}); };
    window.addEventListener("beforeunload", warn);
    window.addEventListener("pagehide", leave);
    return () => {
      window.removeEventListener("beforeunload", warn);
      window.removeEventListener("pagehide", leave);
    };
  }, [generationId, path]);

  const stay = () => {
    if (blocker.state !== "blocked" || cancel.isPending) return;
    cancel.reset();
    onStay?.();
    blocker.reset();
  };
  const dialog = blocker.state === "blocked" && (
    <Dialog open onOpenChange={(open) => {
      if (!open) stay();
    }}>
      <DialogContent showCloseButton={false}
        onOpenAutoFocus={() => {
          previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        }}
        onCloseAutoFocus={(event) => {
          if (previousFocus.current?.isConnected) {
            event.preventDefault();
            previousFocus.current.focus();
          }
        }}
        onEscapeKeyDown={(event) => { if (cancel.isPending) event.preventDefault(); }}
      >
        <DialogHeader>
          <DialogTitle>{generationId ? "Leave and cancel generation?" : "Leave this page?"}</DialogTitle>
          <DialogDescription>{generationId
            ? <>{summaryOnly ? "The repository summary" : "Project context"} is being generated. Leaving this page cancels the job.</>
            : "Generation has ended."}</DialogDescription>
        </DialogHeader>
        <ErrorNotice error={cancel.error} />
        <DialogFooter>
          <Button type="button" variant="outline" disabled={cancel.isPending} onClick={stay}>Stay</Button>
          <Button type="button" variant="destructive" disabled={cancel.isPending} onClick={async () => {
            if (generationId) {
              try {
                await cancel.mutateAsync(generationId);
                onCancelled?.(generationId);
              } catch {
                return;
              }
            }
            blocker.proceed();
          }}>{cancel.isPending ? "Cancelling…" : generationId ? "Cancel" : "Leave"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
  return { dialog, blocked: blocker.state !== "unblocked", handOff };
}
