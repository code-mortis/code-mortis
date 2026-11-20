import { GitPullRequest, ShieldCheck } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { ApiError, useApi, type Schema } from "../api";
import { ErrorNotice } from "./common";
import { Button } from "./ui/button";

const monthFormat = new Intl.DateTimeFormat(undefined, { month: "short", timeZone: "UTC", calendar: "gregory" });
const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC", calendar: "gregory" });
const numberFormat = new Intl.NumberFormat();
const weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const levels = [
  { minimum: 0, label: "0 runs", fill: "bg-[color-mix(in_srgb,var(--foreground)_6%,var(--card))]" },
  { minimum: 1, label: "1–2 runs", fill: "bg-[color-mix(in_srgb,var(--primary)_32%,var(--card))]" },
  { minimum: 3, label: "3–4 runs", fill: "bg-[color-mix(in_srgb,var(--primary)_54%,var(--card))]" },
  { minimum: 5, label: "5–7 runs", fill: "bg-[color-mix(in_srgb,var(--primary)_77%,var(--card))]" },
  { minimum: 8, label: "8 or more runs", fill: "bg-primary" },
];
const weekLayout = { gridTemplateRows: "repeat(7, minmax(0, 1fr))" };
const cardClass = "rounded-xl border bg-card px-4 py-5 sm:px-6";
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

export function RepositoryActivity({ repositoryId }: { repositoryId: number }) {
  const query = useApi<Schema["RepositoryActivity"]>(
    `/repositories/${repositoryId}/activity?${new URLSearchParams({ timezone })}`,
  );
  return (
    <section aria-label="Repository activity" className="min-w-0">
      {query.isPending || query.error ? (
        <div className={cardClass}>
          {query.isPending ? <p role="status" className="text-sm text-muted-foreground">Loading repository activity…</p>
            : query.error && (
              <div className="space-y-3">
                <ErrorNotice error={query.error} title="Could not load repository activity" />
                {(!(query.error instanceof ApiError) || ![401, 403, 404].includes(query.error.status)) &&
                  <Button variant="outline" size="sm" onClick={() => void query.refetch()}>Retry activity</Button>}
              </div>
            )}
        </div>
      ) : <ActivityCalendar days={query.data.days} />}
    </section>
  );
}

function ActivityCalendar({ days }: { days: Schema["RepositoryActivity"]["days"] }) {
  const chart = useRef<HTMLDivElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const tooltip = useRef<HTMLDivElement>(null);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const [focusDay, setFocusDay] = useState(days.length - 1);
  const [active, setActive] = useState<number | null>(null);
  const dates = days.map(({ date }) => new Date(`${date}T00:00:00Z`));
  const offset = dates[0].getUTCDay();
  const weeks = Math.ceil((offset + days.length) / 7);
  const firstMonthStart = dates.findIndex((date) => date.getUTCDate() === 1);
  const columns = { gridTemplateColumns: `repeat(${weeks}, 12px)` };
  const counts = days.reduce((total, day) => ({
    reviews: total.reviews + day.reviews, scans: total.scans + day.scans,
  }), { reviews: 0, scans: 0 });
  const total = counts.reviews + counts.scans;

  useLayoutEffect(() => {
    const viewport = scroll.current!;
    viewport.scrollLeft = viewport.scrollWidth;
    let previousOverflow = viewport.scrollWidth - viewport.clientWidth;
    const resize = new ResizeObserver(() => {
      const overflow = viewport.scrollWidth - viewport.clientWidth;
      if (previousOverflow === 0 && overflow > 0) viewport.scrollLeft = overflow;
      previousOverflow = overflow;
    });
    resize.observe(viewport);
    return () => resize.disconnect();
  }, []);

  useLayoutEffect(() => {
    if (active === null) return;
    const anchor = buttons.current[active]!;
    const panel = tooltip.current!;
    const viewport = scroll.current!;
    const graph = chart.current!;

    function position() {
      const bounds = graph.getBoundingClientRect();
      const cell = anchor.getBoundingClientRect();
      const clip = viewport.getBoundingClientRect();
      const inset = viewport.scrollLeft > 0 ? 36 : 0;
      if (cell.left < clip.left + inset || cell.right > clip.right) {
        setActive(null);
        return;
      }
      const center = cell.left - bounds.left + cell.width / 2;
      panel.style.left = `${Math.max(0, Math.min(bounds.width - panel.offsetWidth, center - panel.offsetWidth / 2))}px`;
      panel.style.top = `${cell.top - bounds.top - panel.offsetHeight - 8}px`;
    }
    position();
    const resize = new ResizeObserver(position);
    resize.observe(graph);
    viewport.addEventListener("scroll", position);
    return () => {
      resize.disconnect();
      viewport.removeEventListener("scroll", position);
    };
  }, [active]);

  useEffect(() => {
    if (active === null) return;
    function dismiss(event: PointerEvent) {
      if (event.target instanceof Node && !chart.current?.contains(event.target)) setActive(null);
    }
    function escape(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") setActive(null);
    }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [active]);

  function move(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const directions: Record<string, number | undefined> = { ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1 };
    const delta = directions[event.key];
    if (delta === undefined) return;
    event.preventDefault();
    const next = buttons.current[index + delta];
    next?.scrollIntoView({ block: "nearest", inline: "nearest" });
    next?.focus({ preventScroll: true });
  }

  const selected = active === null ? null : days[active];
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-1">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground tabular-nums">
          <span className="w-full sm:w-auto"><strong className="mr-1 text-lg font-semibold text-foreground">{numberFormat.format(total)}</strong> total runs</span>
          <span className="inline-flex items-center gap-1.5 sm:border-l sm:pl-3">
            <GitPullRequest aria-hidden="true" className="size-4" />
            <strong className="text-lg font-semibold text-foreground">{numberFormat.format(counts.reviews)}</strong> review runs
          </span>
          <span className="inline-flex items-center gap-1.5 border-l pl-3">
            <ShieldCheck aria-hidden="true" className="size-4" />
            <strong className="text-lg font-semibold text-foreground">{numberFormat.format(counts.scans)}</strong> security scans
          </span>
        </div>
        <p className="ml-auto text-right text-xs text-muted-foreground">
          Last 365 days · {dateFormat.formatRange(dates[0], dates.at(-1)!)}
        </p>
      </div>
      <div className={cardClass}>
        {total === 0 && <p className="mb-3 text-sm text-muted-foreground">No review runs or security scans in this period.</p>}
        <div ref={chart} className="relative" onPointerLeave={() => {
          if (active === null) return;
          const focused = document.activeElement;
          const index = focused instanceof HTMLButtonElement ? buttons.current.indexOf(focused) : -1;
          setActive(index >= 0 && focused?.matches(":focus-visible") ? index : null);
        }}>
          <div ref={scroll} className="overflow-x-auto p-0.5 pb-1 [scroll-padding-inline:38px_2px] [scrollbar-width:thin]">
            <div className="mx-auto grid w-max grid-cols-[36px_auto]">
              <div aria-hidden="true" className="col-start-2 mb-2 grid gap-[3px] text-xs text-muted-foreground" style={columns}>
                {dates.map((date, index) =>
                  (date.getUTCDate() === 1 || (index === 0 && firstMonthStart + offset >= 14)) && (
                    <span key={days[index].date} style={{ gridColumn: Math.floor((offset + index) / 7) + 1 }}>
                      {monthFormat.format(date)}
                    </span>
                  ))}
              </div>
              <div aria-hidden="true" className="sticky left-0 z-10 col-start-1 row-start-2 grid gap-[3px] bg-card text-xs text-muted-foreground shadow-[-2px_0_var(--card)]" style={weekLayout}>
                {weekdays.map((day, index) => <span key={day} className="self-center leading-none">{[1, 3, 5].includes(index) ? day : ""}</span>)}
              </div>
              <div role="group" aria-label="Daily activity. Use the arrow keys to inspect dates." className="col-start-2 row-start-2 grid grid-flow-col gap-[3px]" style={{ ...columns, ...weekLayout }}>
                {days.map((day, index) => {
                  const runs = day.reviews + day.scans;
                  const level = levels.reduce((fill, item) => runs >= item.minimum ? item.fill : fill, levels[0].fill);
                  const date = dates[index];
                  const countLabel = `${numberFormat.format(runs)} total, ${numberFormat.format(day.reviews)} review runs, ${numberFormat.format(day.scans)} security scans`;
                  return <button key={day.date} type="button"
                    ref={(button) => { buttons.current[index] = button; }}
                    aria-label={`${dateFormat.format(date)}. ${countLabel}.`}
                    tabIndex={index === focusDay ? 0 : -1}
                    style={{ gridRow: date.getUTCDay() + 1, gridColumn: Math.floor((offset + index) / 7) + 1 }}
                    className={`aspect-square cursor-default rounded-[3px] focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-foreground forced-colors:border ${level} ${active === index ? "outline-1 outline-offset-1 outline-foreground" : ""}`}
                    onPointerEnter={(event) => { if (event.pointerType === "mouse") setActive(index); }}
                    onFocus={() => { setFocusDay(index); setActive(index); }}
                    onBlur={() => setActive(null)}
                    onClick={() => setActive(index)}
                    onKeyDown={(event) => move(event, index)}
                  />;
                })}
              </div>
            </div>
          </div>
          {selected && <div role="tooltip" ref={tooltip} className="pointer-events-none absolute z-20 w-max max-w-full min-w-44 rounded-lg border bg-popover p-2.5 text-xs text-popover-foreground shadow-md tabular-nums">
            <p className="mb-1.5 font-semibold">{dateFormat.format(dates[active!])} · {numberFormat.format(selected.reviews + selected.scans)} {selected.reviews + selected.scans === 1 ? "run" : "runs"}</p>
            <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5">
              <dt className="text-muted-foreground">Review runs</dt><dd className="text-right font-semibold">{numberFormat.format(selected.reviews)}</dd>
              <dt className="text-muted-foreground">Security scans</dt><dd className="text-right font-semibold">{numberFormat.format(selected.scans)}</dd>
            </dl>
          </div>}
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t pt-3 text-xs text-muted-foreground">
          <p>Runs counted on the day they were created</p>
          <div role="group" className="flex items-center gap-1.5" aria-label={`Runs per day. ${levels.map((level) => level.label).join(", ")}.`}>
            <span aria-hidden="true">Runs per day</span><span aria-hidden="true">0</span>
            {levels.map((level) => <span key={level.minimum} aria-hidden="true" title={level.label} className={`size-3 rounded-[3px] ${level.fill}`} />)}
            <span aria-hidden="true">8+</span>
          </div>
        </div>
      </div>
    </>
  );
}
