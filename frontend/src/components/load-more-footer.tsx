import { ErrorNotice } from "./common";
import { Button } from "./ui/button";

type PageQuery = {
  data?: { pages: unknown[] };
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  isFetchNextPageError: boolean;
  isFetching: boolean;
  error: Error | null;
  fetchNextPage: () => unknown;
  refetch: () => unknown;
};

export function LoadMoreFooter({ query, shown, total, label }: {
  query: PageQuery; shown: number; total: number; label: string;
}) {
  const paginated = query.hasNextPage || (query.data?.pages.length ?? 0) > 1;
  if (!paginated && !query.error) return null;
  return <div className="space-y-2 px-5 py-3" data-slot="load-more-footer">
    <ErrorNotice error={query.error} />
    <div className="flex items-center justify-between gap-3">
      {query.hasNextPage ? <Button variant="ghost" size="sm" disabled={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>
        {query.isFetchingNextPage ? "Loading…" : query.isFetchNextPageError ? "Retry" : "Load more"}<span className="sr-only"> {label}</span>
      </Button> : query.error ? <Button variant="outline" size="sm" disabled={query.isFetching} onClick={() => query.refetch()}>Retry {label}</Button> : null}
      {paginated && <span role="status" className="ml-auto text-xs tabular-nums text-muted-foreground">{shown} of {total}</span>}
    </div>
  </div>;
}
