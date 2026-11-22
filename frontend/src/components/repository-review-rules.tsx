import { useEffect, useId, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { api, type Repository, type Schema } from "../api";
import { ErrorNotice, Field, PageTitle } from "./common";
import { Button } from "./ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";
import { Switch } from "./ui/switch";
import { Textarea } from "./ui/textarea";

type Rule = Required<Schema["ReviewRule"]>;

function asDraft(rules: Schema["ReviewRule"][] = []): Rule[] {
  return rules.map(({ glob = "", active = true, ...rule }) => ({ ...rule, glob, active }));
}

export function RepositoryReviewRules({ repository }: { repository: Repository }) {
  const id = useId();
  const endpoint = `/repositories/${repository.id}/review-rules`;
  const cache = useQueryClient();
  const save = useMutation({
    mutationFn: async (body: Schema["ReviewRulesUpdate"]) => asDraft(await api<Schema["ReviewRule"][]>(endpoint, "PUT", body)),
    onSuccess: async (next) => {
      await cache.cancelQueries({ queryKey: ["/repositories"], exact: true });
      cache.setQueryData<Repository[]>(["/repositories"], (repositories) => repositories?.map((row) =>
        row.id === repository.id ? { ...row, review_rules: next } : row));
    },
  });
  const [saved, setSaved] = useState(() => asDraft(repository.review_rules));
  const [rules, setRules] = useState(saved);
  const [editor, setEditor] = useState<{ index: number | null; draft: Rule } | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [saveError, setSaveError] = useState<unknown>();
  const addButton = useRef<HTMLButtonElement>(null);
  const editorTrigger = useRef<HTMLButtonElement>(null);
  const deleteTrigger = useRef<HTMLButtonElement>(null);
  const dirty = JSON.stringify(rules) !== JSON.stringify(saved);
  const editing = editor?.index != null;
  const canSubmit = editor && (editing || rules.length < 50) && editor.draft.name.trim() && editor.draft.instructions.trim();

  useEffect(() => {
    if (dirty || save.isPending || editing || deleting !== null) return;
    const incoming = asDraft(repository.review_rules);
    if (JSON.stringify(incoming) === JSON.stringify(saved)) return;
    setSaved(incoming);
    setRules(incoming);
  }, [deleting, dirty, editing, repository.review_rules, save.isPending, saved]);

  function change(index: number, draft: Partial<Rule>) {
    setRules(rules.map((rule, i) => i === index ? { ...rule, ...draft } : rule));
    setSaveError(undefined);
  }

  async function persistRules(next: Rule[], apply: (savedRules: Rule[]) => void, message: string) {
    setSaveError(undefined);
    try {
      const persisted = await save.mutateAsync({ rules: next, expected_rules: saved });
      setSaved(persisted);
      apply(persisted);
      toast.success(message, { position: "top-right" });
    } catch (error) {
      setSaveError(error);
    }
  }

  function openEditor(index: number | null, trigger: HTMLButtonElement) {
    editorTrigger.current = trigger;
    setSaveError(undefined);
    setEditor({ index, draft: index === null ? { name: "", instructions: "", glob: "", active: true } : saved[index] });
  }

  function changeDraft(changes: Partial<Rule>) {
    setEditor((current) => current && { ...current, draft: { ...current.draft, ...changes } });
    setSaveError(undefined);
  }

  function saveRule() {
    if (!editor || !canSubmit || save.isPending) return;
    const { index, draft } = editor;
    const next = index === null ? [...saved, draft] : saved.map((rule, i) => i === index ? draft : rule);
    void persistRules(next, (persisted) => {
      // Keep pending activation changes in the list when saving this rule's fields.
      setRules(persisted.map((rule, i) => ({ ...rule, active: rules[i]?.active ?? rule.active })));
      setEditor(null);
    }, index === null ? "Rule added." : "Rule saved.");
  }

  function deleteRule(index: number) {
    void persistRules(saved.filter((_, i) => i !== index), () => {
      setRules(rules.filter((_, i) => i !== index));
      deleteTrigger.current = null;
      setDeleting(null);
    }, "Rule deleted.");
  }

  return <section aria-labelledby={`${id}-heading`} className="min-w-0 space-y-6">
    <PageTitle title="Custom rules" headingProps={{ id: `${id}-heading` }} actions={
      <Button ref={addButton} type="button" variant="outline" aria-haspopup="dialog" disabled={save.isPending || rules.length >= 50}
        onClick={(event) => openEditor(null, event.currentTarget)}>
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
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto" showCloseButton={!save.isPending} onCloseAutoFocus={(event) => {
        event.preventDefault();
        editorTrigger.current?.focus();
      }}>
        <DialogHeader>
          <DialogTitle>{editing ? "Edit rule" : "Add rule"}</DialogTitle>
          <DialogDescription>Give Mortis instructions for reviewing matching files.</DialogDescription>
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
          <DialogFooter>
            <DialogClose asChild><Button type="button" variant="outline" disabled={save.isPending}>Cancel</Button></DialogClose>
            <Button type="submit" disabled={save.isPending || !canSubmit}>
              {save.isPending ? "Saving…" : editing ? "Save" : "Add rule"}
            </Button>
          </DialogFooter>
        </form>}
      </DialogContent>
    </Dialog>

    {rules.length > 0 ? <ul aria-label="Repository review rules" className="divide-y rounded-lg border bg-card">
      {rules.map((rule, index) => {
        const name = rule.name.trim() || "Untitled rule";
        return <li key={index} className="space-y-4 p-4">
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
            <div className="min-w-0 flex-1 basis-36">
              <p className="break-words text-sm font-medium">{name}</p>
              <p className="break-all text-xs text-muted-foreground">{rule.glob ? <code>{rule.glob}</code> : "All files"}</p>
              <p className="mt-2 line-clamp-2 whitespace-pre-wrap break-words text-sm text-muted-foreground">{rule.instructions}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <Switch aria-label={`Active rule ${name}`} checked={rule.active} disabled={save.isPending}
                  onCheckedChange={(active) => change(index, { active })} />
                {rule.active ? "Active" : "Inactive"}
              </label>
              <Button type="button" variant="ghost" size="sm" disabled={save.isPending} aria-haspopup="dialog"
                aria-label={`Edit ${name}`} onClick={(event) => openEditor(index, event.currentTarget)}>Edit</Button>
              <Button type="button" variant="ghost" size="sm" disabled={save.isPending} aria-label={`Delete ${name}`} aria-haspopup="dialog"
                onClick={(event) => {
                  deleteTrigger.current = event.currentTarget;
                  setSaveError(undefined);
                  setDeleting(index);
                }}>Delete</Button>
            </div>
          </div>
        </li>;
      })}
    </ul> : <p className="text-sm text-muted-foreground">Add a rule to supply your own review instructions.</p>}

    {repository.settings.rules && Object.keys(repository.settings.rules).length > 0 &&
      <p className="text-sm text-muted-foreground">Legacy repository rules still apply.</p>}

    <Dialog open={deleting !== null} onOpenChange={(open) => {
      if (!open && !save.isPending) {
        setSaveError(undefined);
        setDeleting(null);
      }
    }}>
      <DialogContent showCloseButton={!save.isPending} onCloseAutoFocus={(event) => {
        event.preventDefault();
        (deleteTrigger.current?.isConnected ? deleteTrigger.current : addButton.current)?.focus();
      }}>
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

    {dirty && <footer className="sticky bottom-0 z-10 space-y-3 bg-background py-4">
      {!editor && deleting === null && <ErrorNotice error={saveError} />}
      <div className="flex items-center justify-end gap-2">
        <p role="status" className="mr-auto text-sm text-muted-foreground">Unsaved changes</p>
        <Button type="button" disabled={save.isPending} onClick={() => void persistRules(rules, setRules, "Custom rules saved.")}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </footer>}
  </section>;
}
