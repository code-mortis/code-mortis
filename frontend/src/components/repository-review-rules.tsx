import { useEffect, useId, useLayoutEffect, useRef, useState, type Ref } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { api, type Repository, type Schema } from "../api";
import { ErrorNotice, Field, PageTitle } from "./common";
import { Button } from "./ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "./ui/dialog";
import { Input } from "./ui/input";
import { Switch } from "./ui/switch";
import { Textarea } from "./ui/textarea";

type Rule = Required<Schema["ReviewRule"]>;

function asDraft(rules: Schema["ReviewRule"][] = []): Rule[] {
  return rules.map(({ glob = "", active = true, ...rule }) => ({ ...rule, glob, active }));
}

function RuleFields({ rule, disabled, onChange, nameRef }: {
  rule: Rule;
  disabled: boolean;
  onChange: (changes: Partial<Rule>) => void;
  nameRef?: Ref<HTMLInputElement>;
}) {
  return <>
    <Field label="Name">{(id) => <Input ref={nameRef} id={id} required maxLength={150} value={rule.name}
      disabled={disabled} onChange={(event) => onChange({ name: event.target.value })} />}</Field>
    <Field label="Instructions">{(id) => <Textarea id={id} required rows={4} maxLength={4000} value={rule.instructions}
      disabled={disabled} onChange={(event) => onChange({ instructions: event.target.value })} />}</Field>
    <Field label="Glob pattern (optional)" hint="Leave blank to match every file.">{(id) => <Input id={id}
      maxLength={500} placeholder="src/**/*.py" value={rule.glob} disabled={disabled}
      onChange={(event) => onChange({ glob: event.target.value })} />}</Field>
  </>;
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
  const [editing, setEditing] = useState<number | null>(null);
  const [newRule, setNewRule] = useState<Rule | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [saveError, setSaveError] = useState<unknown>();
  const nameInput = useRef<HTMLInputElement>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  const deleteTrigger = useRef<HTMLButtonElement>(null);
  const dirty = JSON.stringify(rules) !== JSON.stringify(saved);

  useLayoutEffect(() => {
    if (editing !== null) nameInput.current?.focus();
  }, [editing]);

  useEffect(() => {
    if (dirty || save.isPending || deleting !== null) return;
    const incoming = asDraft(repository.review_rules);
    if (JSON.stringify(incoming) === JSON.stringify(saved)) return;
    setSaved(incoming);
    setRules(incoming);
    setEditing(null);
  }, [deleting, dirty, repository.review_rules, save.isPending, saved]);

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

  function saveRules() {
    const invalid = rules.findIndex((rule) => !rule.name.trim() || !rule.instructions.trim());
    if (invalid >= 0) {
      setEditing(invalid);
      setSaveError(new Error("Enter a name and instructions for each rule."));
      return;
    }
    void persistRules(rules, (persisted) => {
      setRules(persisted);
      setEditing(null);
    }, "Custom rules saved.");
  }

  function deleteRule(index: number) {
    void persistRules(saved.filter((_, i) => i !== index), () => {
      setRules(rules.filter((_, i) => i !== index));
      setEditing(editing === index ? null : editing !== null && editing > index ? editing - 1 : editing);
      deleteTrigger.current = null;
      setDeleting(null);
    }, "Rule deleted.");
  }

  return <section aria-labelledby={`${id}-heading`} className="min-w-0 space-y-6">
    <Dialog open={newRule !== null} onOpenChange={(open) => {
      if (save.isPending) return;
      setSaveError(undefined);
      setNewRule(open ? { name: "", instructions: "", glob: "", active: true } : null);
    }}>
      <PageTitle title="Custom rules" headingProps={{ id: `${id}-heading` }} actions={
        <DialogTrigger asChild>
          <Button ref={addButton} type="button" variant="outline" disabled={save.isPending || rules.length >= 50}>
            <Plus aria-hidden="true" /> Add rule
          </Button>
        </DialogTrigger>
      }>
        Give Mortis instructions for matching files in pull request reviews.
      </PageTitle>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto" showCloseButton={!save.isPending}>
        <DialogHeader>
          <DialogTitle>Add rule</DialogTitle>
          <DialogDescription>Give Mortis instructions for reviewing matching files.</DialogDescription>
        </DialogHeader>
        {newRule && <form className="space-y-4" onSubmit={(event) => {
          event.preventDefault();
          if (save.isPending || rules.length >= 50 || !newRule.name.trim() || !newRule.instructions.trim()) return;
          void persistRules([...saved, newRule], (persisted) => {
            setRules([...rules, ...persisted.slice(saved.length)]);
            setNewRule(null);
          }, "Rule added.");
        }}>
          <RuleFields rule={newRule} disabled={save.isPending} onChange={(changes) => {
            setNewRule({ ...newRule, ...changes });
            setSaveError(undefined);
          }} />
          <ErrorNotice error={saveError} />
          <DialogFooter>
            <DialogClose asChild><Button type="button" variant="outline" disabled={save.isPending}>Cancel</Button></DialogClose>
            <Button type="submit" disabled={save.isPending || rules.length >= 50 || !newRule.name.trim() || !newRule.instructions.trim()}>
              {save.isPending ? "Adding…" : "Add rule"}
            </Button>
          </DialogFooter>
        </form>}
      </DialogContent>
    </Dialog>

    {rules.length > 0 ? <ul aria-label="Repository review rules" className="divide-y rounded-lg border bg-card">
      {rules.map((rule, index) => {
        const open = editing === index;
        const name = rule.name.trim() || "Untitled rule";
        return <li key={index} className="space-y-4 p-4">
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
            <div className="min-w-0 flex-1 basis-36">
              <p className="break-words text-sm font-medium">{name}</p>
              <p className="break-all text-xs text-muted-foreground">{rule.glob ? <code>{rule.glob}</code> : "All files"}</p>
              {!open && <p className="mt-2 line-clamp-2 whitespace-pre-wrap break-words text-sm text-muted-foreground">{rule.instructions}</p>}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <Switch aria-label={`Active rule ${name}`} checked={rule.active} disabled={save.isPending}
                  onCheckedChange={(active) => change(index, { active })} />
                {rule.active ? "Active" : "Inactive"}
              </label>
              {open ? <Button type="button" variant="ghost" size="sm" disabled={save.isPending} onClick={() => {
                setEditing(null);
                requestAnimationFrame(() => document.getElementById(`${id}-edit-${index}`)?.focus());
              }}>Done</Button> : <Button id={`${id}-edit-${index}`} type="button" variant="ghost" size="sm" disabled={save.isPending}
                aria-label={`Edit ${name}`} onClick={() => setEditing(index)}>Edit</Button>}
              <Button type="button" variant="ghost" size="sm" disabled={save.isPending} aria-label={`Delete ${name}`} aria-haspopup="dialog"
                onClick={(event) => {
                  deleteTrigger.current = event.currentTarget;
                  setSaveError(undefined);
                  setDeleting(index);
                }}>Delete</Button>
            </div>
          </div>
          {open && <div className="space-y-4 border-t pt-4">
            <RuleFields rule={rule} disabled={save.isPending} nameRef={nameInput} onChange={(changes) => change(index, changes)} />
          </div>}
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

    <footer className="sticky bottom-0 z-10 space-y-3 bg-background py-4">
      {!newRule && deleting === null && <ErrorNotice error={saveError} />}
      <div className="flex items-center justify-end gap-2">
        <p role="status" className="mr-auto text-sm text-muted-foreground empty:sr-only">{dirty ? "Unsaved changes" : ""}</p>
        <Button type="button" disabled={!dirty || save.isPending} onClick={saveRules}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </footer>
  </section>;
}
