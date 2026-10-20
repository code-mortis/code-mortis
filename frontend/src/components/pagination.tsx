import { ChevronLeft, ChevronRight } from "lucide-react";
import { ErrorNotice } from "./common";
import { Button } from "./ui/button";

export function Pagination({ page, pageSize, total, loading = false, error = null, onPageChange, onRetry, label }: {
  page: number;
  pageSize: number;
  total: number;
  loading?: boolean;
  error?: Error | null;
  onPageChange: (page: number) => void;
  onRetry?: () => unknown;
  label: string;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages === 1 && page === 1 && !error) return null;
  return <div className="space-y-2 px-5 py-3" data-slot="pagination">
    <ErrorNotice error={error} />
    {error && onRetry && <Button variant="outline" size="sm" disabled={loading} onClick={() => onRetry()}>Retry {label}</Button>}
    {(pages > 1 || page > 1) && <nav aria-label={`${label} pagination`} aria-busy={loading} className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" className="aria-disabled:pointer-events-none aria-disabled:opacity-50" disabled={page <= 1} aria-disabled={loading || page <= 1} onClick={() => { if (!loading) onPageChange(page - 1); }}><ChevronLeft aria-hidden="true" className="size-4" />Previous<span className="sr-only"> {label}</span></Button>
        <Button variant="ghost" size="sm" className="aria-disabled:pointer-events-none aria-disabled:opacity-50" disabled={page >= pages} aria-disabled={loading || page >= pages} onClick={() => { if (!loading) onPageChange(page + 1); }}>Next<span className="sr-only"> {label}</span><ChevronRight aria-hidden="true" className="size-4" /></Button>
      </div>
      <span role="status" className="ml-auto text-xs tabular-nums text-muted-foreground">Page {page} of {pages} · {total} total</span>
    </nav>}
  </div>;
}
