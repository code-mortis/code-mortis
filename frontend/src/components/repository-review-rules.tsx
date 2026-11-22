import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { MoreHorizontal, Plus } from "lucide-react";
import { useLocation, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { api, type Repository, type Schema } from "../api";
import { INBOX_QUERY_MAX_LENGTH } from "../lib/inbox-filters";
import { normalizeOrdering } from "../lib/ordering";
import { Empty, ErrorNotice, Field, PageTitle } from "./common";
import { FilterBar } from "./filter-bar";
import { Button } from "./ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "./ui/dropdown-menu";
import { Input } from "./ui/input";
import { Switch } from "./ui/switch";
import { Textarea } from "./ui/textarea";

type Rule = Required<Schema["ReviewRule"]>;
const ruleOrderings = { position: "Rule order", name: "Name" };

function normalizeRules(rules: Schema["ReviewRule"][] = []): Rule[] {
  return rules.map(({ glob = "", active = true, ...rule }) => ({ ...rule, glob, active }));
}

export function RepositoryReviewRules({ repository }: { repository: Repository }) {
  const id = useId();
  const endpoint = `/repositories/${repository.id}/review-rules`;
  const { state } = useLocation();
  const [params, setParams] = useSearchParams();
  const searchInput = useRef<HTMLInputElement>(null);
  const [searchResetKey, setSearchResetKey] = useState(0);
  const cache = useQueryClient();
  const save = useMutation({
    mutationFn: async (body: Schema["ReviewRulesUpdate"]) => normalizeRules(await api<Schema["ReviewRule"][]>(endpoint, "PUT", body)),
    onSuccess: async (next) => {
      await cache.cancelQueries({ queryKey: ["/repositories"], exact: true });
      cache.setQueryData<Repository[]>(["/repositories"], (repositories) => repositories?.map((row) =>
        row.id === repository.id ? { ...row, review_rules: next } : row));
    },
  });
  const [rules, setRules] = useState(() => normalizeRules(repository.review_rules));
  const [editor, setEditor] = useState<{ index: number | null; draft: Rule } | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [saveError, setSaveError] = useState<unknown>();
  const addButton = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef<HTMLButtonElement>(null);
  const editing = editor?.index != null;
  const canSubmit = editor && (editing || rules.length < 50) && editor.draft.name.trim() && editor.draft.instructions.trim();
  const query = (params.get("q") ?? "").trim().slice(0, INBOX_QUERY_MAX_LENGTH);
  const needle = query.toLowerCase();
  const statuses = [...new Set(params.getAll("status").filter((value) => value === "active" || value === "inactive"))];
  const ordering = normalizeOrdering(params.get("ordering"), ruleOrderings, "position");
  const matches = rules.map((rule, index) => ({ rule, index })).filter(({ rule }) =>
    [rule.name, rule.instructions, rule.glob].some((value) => value.toLowerCase().includes(needle)));
  const activeCount = matches.filter(({ rule }) => rule.active).length;
  const rows = matches.filter(({ rule }) => !statuses.length || statuses.includes(rule.active ? "active" : "inactive"));
  if (ordering === "name" || ordering === "-name") {
    const direction = ordering === "name" ? 1 : -1;
    rows.sort((a, b) => direction * a.rule.name.localeCompare(b.rule.name, undefined, { sensitivity: "base" }));
  } else if (ordering === "-position") rows.reverse();

  useEffect(() => {
    if (save.isPending || editing || deleting !== null) return;
    setRules(normalizeRules(repository.review_rules));
  }, [deleting, editing, repository.review_rules, save.isPending]);

  const search = useCallback((value: string) => {
    const next = new URLSearchParams(params);
    if (value.trim()) next.set("q", value.trim()); else next.delete("q");
    setParams(next, { replace: true, state });
  }, [params, setParams, state]);

  function clearFilters() {
    const next = new URLSearchParams(params);
    next.delete("q");
    next.delete("status");
    setParams(next, { state });
    setSearchResetKey((current) => current + 1);
    searchInput.current?.focus();
  }

  async function persistRules(next: Rule[], message: string, onSaved?: () => void) {
    setSaveError(undefined);
    try {
      setRules(await save.mutateAsync({ rules: next, expected_rules: rules }));
      onSaved?.();
      toast.success(message);
    } catch (error) {
      setSaveError(error);
    }
  }

  function openEditor(index: number | null) {
    setSaveError(undefined);
    setEditor({ index, draft: index === null ? { name: "", instructions: "", glob: "", active: true } : rules[index] });
  }

  function changeDraft(changes: Partial<Rule>) {
    setEditor((current) => current && { ...current, draft: { ...current.draft, ...changes } });
    setSaveError(undefined);
  }

  function saveRule() {
    if (!editor || !canSubmit || save.isPending) return;
    const { index, draft } = editor;
    const next = index === null ? [...rules, draft] : rules.map((rule, i) => i === index ? draft : rule);
    void persistRules(next, index === null ? "Rule added." : "Rule saved.", () => setEditor(null));
  }

  function deleteRule(index: number) {
    void persistRules(rules.filter((_, i) => i !== index), "Rule deleted.", () => {
      returnFocus.current = null;
      setDeleting(null);
    });
  }

  function restoreFocus(event: Event) {
    event.preventDefault();
    const trigger = returnFocus.current;
    if (trigger?.isConnected && !trigger.disabled) trigger.focus();
    else addButton.current?.focus();
  }

  return <section aria-labelledby={`${id}-heading`} className="min-w-0">
    <PageTitle title="Custom rules" headingProps={{ id: `${id}-heading` }} actions={
      <Button ref={addButton} type="button" aria-haspopup="dialog" disabled={save.isPending || rules.length >= 50}
        onClick={(event) => {
          returnFocus.current = event.currentTarget;
          openEditor(null);
        }}>
        <Plus aria-hidden="true" /> Add rule
      </Button>
    }>
      Give Mortis instructions for matching files in pull request reviews.
    </PageTitle>
    <Dialog open={editor !== null} onOpenChange={(open) => {
      if (!open && !save.isPending) {
        setSaveError(undefined);
        setEditor(null);
      }
    }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto" showCloseButton={!save.isPending} onCloseAutoFocus={restoreFocus}>
        <DialogHeader>
          <DialogTitle>{editing ? "Edit rule" : "Add rule"}</DialogTitle>
          <DialogDescription>Set the instructions and optional glob pattern for this rule.</DialogDescription>
        </DialogHeader>
        {editor && <form className="space-y-4" onSubmit={(event) => {
          event.preventDefault();
          saveRule();
        }}>
          <Field label="Name">{(id) => <Input id={id} required maxLength={150} value={editor.draft.name}
            disabled={save.isPending} onChange={(event) => changeDraft({ name: event.target.value })} />}</Field>
          <Field label="Instructions">{(id) => <Textarea id={id} required rows={4} maxLength={4000} value={editor.draft.instructions}
            disabled={save.isPending} onChange={(event) => changeDraft({ instructions: event.target.value })} />}</Field>
          <Field label="Glob pattern (optional)" hint="Leave blank to match every file.">{(id) => <Input id={id}
            maxLength={500} placeholder="src/**/*.py" value={editor.draft.glob} disabled={save.isPending}
            onChange={(event) => changeDraft({ glob: event.target.value })} />}</Field>
          <ErrorNotice error={saveError} />
          <DialogFooter className="border-t pt-4">
            <DialogClose asChild><Button type="button" variant="outline" disabled={save.isPending}>Cancel</Button></DialogClose>
            <Button type="submit" disabled={save.isPending || !canSubmit}>
              {save.isPending ? "Saving…" : editing ? "Save" : "Add rule"}
            </Button>
          </DialogFooter>
        </form>}
      </DialogContent>
    </Dialog>

    <div className="mb-6">
      <FilterBar query={query} searchResetKey={searchResetKey} searchInputRef={searchInput}
        searchLabel="Search custom rules" searchPlaceholder="Search rules…" onSearch={search}
        filters={[{ key: "status", label: "Status", values: statuses, choices: [
          { value: "active", name: "Active", count: activeCount },
          { value: "inactive", name: "Inactive", count: matches.length - activeCount },
        ] }]}
        onChange={(_, values) => {
          const next = new URLSearchParams(params);
          next.delete("status");
          values.forEach((value) => next.append("status", value));
          setParams(next, { state });
        }}
        ordering={{ value: ordering, defaultValue: "position", options: ruleOrderings, onChange: (value) => {
          const next = new URLSearchParams(params);
          if (value === "position") next.delete("ordering"); else next.set("ordering", value);
          setParams(next, { state });
        } }}>
        <span role="status" className="sr-only">{rows.length} of {rules.length} rules</span>
      </FilterBar>
    </div>
    {!!saveError && !editor && deleting === null && <div className="mb-4"><ErrorNotice error={saveError} /></div>}
    {rows.length > 0 ? <div className="settings-container overflow-hidden rounded-lg border bg-card">
      <div aria-hidden="true" className="settings-record settings-rule-row settings-list-heading border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground">
        <span>Rule</span><span>Glob pattern</span><span>Status</span><span />
      </div>
      <ul aria-label="Repository review rules" className="divide-y">
        {rows.map(({ rule, index }) => {
          const name = rule.name.trim() || "Untitled rule";
          return <li key={index} className="settings-record settings-rule-row px-4 py-4 text-sm sm:px-5">
            <div className="settings-identity min-w-0">
              <h2 className="break-words font-medium [overflow-wrap:anywhere]">{name}</h2>
              <p className="mt-1 line-clamp-2 whitespace-pre-wrap break-words leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">{rule.instructions}</p>
            </div>
            <div className="text-xs leading-relaxed">
              <span className="settings-cell-label mb-1 text-muted-foreground">Glob pattern</span>
              {rule.glob ? <code className="break-all">{rule.glob}</code> : <span className="text-muted-foreground">All files</span>}
            </div>
            <div className="text-xs text-muted-foreground">
              <span className="settings-cell-label mb-1">Status</span>
              <Switch aria-label={`Active rule ${name}`} checked={rule.active} disabled={save.isPending}
                onCheckedChange={(active) => void persistRules(
                  rules.map((item, i) => i === index ? { ...item, active } : item),
                  active ? "Rule activated." : "Rule deactivated.",
                )} />
            </div>
            <div className="settings-actions flex items-center justify-end">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button type="button" variant="ghost" size="icon" className="size-11 text-muted-foreground sm:size-8"
                    disabled={save.isPending} aria-label={`Actions for ${name}`}
                    onPointerDown={(event) => { returnFocus.current = event.currentTarget; }}
                    onFocus={(event) => { returnFocus.current = event.currentTarget; }}>
                    <MoreHorizontal aria-hidden="true" className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="[&_[role=menuitem]]:min-h-11 sm:[&_[role=menuitem]]:min-h-8"
                  onCloseAutoFocus={(event) => { if (editor || deleting !== null) event.preventDefault(); }}>
                  <DropdownMenuItem onSelect={() => openEditor(index)}>Edit</DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive" onSelect={() => {
                    setSaveError(undefined);
                    setDeleting(index);
                  }}>Delete</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </li>;
        })}
      </ul>
    </div> : rules.length > 0 ? <Empty title="No matching rules">
      Try a different search or status filter.
      <div><Button variant="outline" className="mt-4" onClick={clearFilters}>Clear filters</Button></div>
    </Empty> : <Empty title="No custom rules yet">Add a rule to apply review instructions to all files or to paths that match a glob.</Empty>}

    {repository.settings.rules && Object.keys(repository.settings.rules).length > 0 &&
      <p className="mt-4 text-xs leading-relaxed text-muted-foreground">Legacy repository rules still apply.</p>}

    <Dialog open={deleting !== null} onOpenChange={(open) => {
      if (!open && !save.isPending) {
        setSaveError(undefined);
        setDeleting(null);
      }
    }}>
      <DialogContent showCloseButton={!save.isPending} onCloseAutoFocus={restoreFocus}>
        <DialogHeader>
          <DialogTitle>Delete rule?</DialogTitle>
          <DialogDescription className="break-words">
            Delete {deleting !== null ? rules[deleting]?.name.trim() || "this rule" : "this rule"} from this repository?
          </DialogDescription>
        </DialogHeader>
        <ErrorNotice error={saveError} />
        <DialogFooter>
          <DialogClose asChild><Button type="button" variant="outline" disabled={save.isPending}>Cancel</Button></DialogClose>
          <Button type="button" variant="destructive" disabled={save.isPending} onClick={() => {
            if (deleting !== null) deleteRule(deleting);
          }}>{save.isPending ? "Deleting…" : "Delete rule"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

  </section>;
}
