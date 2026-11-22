import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowUpRight, Bot, Check, Copy, FolderGit2, GitPullRequest, Github, KeyRound, MoreHorizontal, Network, Palette, Plug, Plus, Shield, Users } from "lucide-react";
import {
  api,
  useAction,
  useApi,
  type Connection,
  type Repository,
  type Schema,
} from "./api";
import { ModelPicker } from "./components/model-picker";
import { SearchSelect } from "./components/search-select";
import { BackgroundField } from "./components/background-field";
import { GenerationActivity } from "./components/generation-activity";
import { savedBackgroundInputs, type BackgroundHandoff } from "./hooks/use-background-generation";
import { CodeGraphSettings } from "./components/code-graph-settings";
import { SettingsNavigation } from "./components/settings-navigation";
import { GitHubInstallations } from "./components/github-installations";
import { Button } from "./components/ui/button";
import { AppearanceSettings } from "./components/appearance-settings";
import { ApiTokensSection, CreateApiTokenPage } from "./components/api-tokens";
import { UserAvatar } from "./components/user-avatar";
import { McpSettingsSection } from "./mcp";
import type { ColorMode, ThemeId } from "./lib/appearance";
import { Badge } from "./components/ui/badge";
import { Input } from "./components/ui/input";
import { Textarea } from "./components/ui/textarea";
import { Switch } from "./components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "./components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "./components/ui/dialog";
import {
  date,
  ErrorNotice,
  Field,
  Loading,
  PageTitle,
  Status,
} from "./components/common";

type Provider = {
  name: string;
  displayName: string;
  protocol: string;
  baseUrl: string;
  ambientAuth?: boolean;
};
type Instance = {
  github_app: { id: string; slug: string; install_url: string };
  installations: { id: number; account: string; active: boolean }[];
  members: { id: number; login: string; role: string; avatar_url?: string }[];
  failed_jobs: { id: string; kind: string; error: string; available_at?: string | null; attempts?: number }[];
  public_url: string;
  webhook_url?: string;
  configuration_errors?: string[];
};
const protocols = [
  "openai",
  "openai-responses",
  "anthropic",
  "anthropic-bedrock",
] as const;
const apiProviderLogos = new Map([
  ["anthropic", "claude"],
  ["baidu-qianfan", "baiducloud"],
  ["bedrock", "bedrock"],
  ["dashscope", "alibabacloud"],
  ["dashscope-tokenplan", "alibabacloud"],
  ["deepseek", "deepseek"],
  ["edenai", "edenai"],
  ["gemini", "gemini"],
  ["hy-tokenplan", "hunyuan"],
  ["iflytek", "iflytekcloud"],
  ["kimi", "kimi"],
  ["kimi-global", "kimi"],
  ["litellm", "litellm"],
  ["mimo", "xiaomimimo"],
  ["minimax", "minimax"],
  ["minimax-cn", "minimax"],
  ["mistral", "mistral"],
  ["novita", "novita"],
  ["ollama-cloud", "ollama"],
  ["openai", "openai"],
  ["openai-responses", "openai"],
  ["openrouter", "openrouter"],
  ["siliconflow", "siliconcloud"],
  ["siliconflow-cn", "siliconcloud"],
  ["tencent-tokenhub", "tencent"],
  ["volcengine", "volcengine"],
  ["xai", "xai"],
  ["z-ai", "zai"],
  ["z-ai-coding", "zai"],
  ["opencode", "opencode"],
]);
function ProviderLogo({ logo, className = "" }: { logo: string; className?: string }) {
  return <img
    src={`/brands/${logo}.svg`}
    alt="" aria-hidden="true" width={16} height={16}
    className={`size-4 shrink-0 ${logo === "claude" ? "" : logo === "litellm" ? "dark:brightness-0 dark:invert" : "dark:invert"} ${className}`}
  />;
}
const languages = ["Arabic", "Bengali", "Bulgarian", "Catalan", "Chinese (Simplified)", "Chinese (Traditional)", "Croatian", "Czech", "Danish", "Dutch", "English", "Finnish", "French", "German", "Greek", "Hebrew", "Hindi", "Hungarian", "Indonesian", "Italian", "Japanese", "Korean", "Malay", "Norwegian", "Persian", "Polish", "Portuguese", "Romanian", "Russian", "Serbian", "Slovak", "Spanish", "Swedish", "Tamil", "Thai", "Turkish", "Ukrainian", "Urdu", "Vietnamese"];
function parseObject(value: string, label: string) {
  let data: unknown;
  try {
    data = JSON.parse(value || "{}");
  } catch {
    throw new Error(`${label} must contain valid JSON. Check quotes, commas, and braces, then try again.`);
  }
  if (!data || typeof data !== "object" || Array.isArray(data))
    throw new Error(`${label} must be a JSON object.`);
  return data;
}

function memberRoleLabel(role: string) {
  if (role === "admin") return "Administrator";
  if (role === "member") return "Member";
  return role;
}

const reviewSensitivities = [
  { value: "low_noise", label: "Low noise", description: "Material bugs with likely triggers." },
  { value: "balanced", label: "Balanced", description: "Actionable bugs and edge cases." },
  { value: "broad", label: "Broad", description: "Also minor bugs and rare cases." },
] as const;

function ExecutionLimitField({ id, label, hint, min, max, value, fallback, unsetLabel = "Unlimited", onChange, onInvalidDraft }: {
  id: string;
  label: string;
  hint: string;
  min: number;
  max?: number;
  value: number | null;
  fallback?: number;
  unsetLabel?: string;
  onChange: (value: number | null) => void;
  onInvalidDraft: (id: string, invalid: boolean) => void;
}) {
  const limited = useRef(value ?? fallback ?? min);
  return <div className="grid gap-4 p-5 sm:grid-cols-[minmax(0,1fr)_160px] sm:gap-6">
    <div className="space-y-2">
      <label htmlFor={id} className="text-sm font-medium">{label}</label>
      <p id={`${id}-hint`} className="text-xs leading-relaxed text-muted-foreground">{hint}</p>
    </div>
    <div className="space-y-2">
      <Input
        key={value === null ? "unlimited" : "limited"}
        id={id}
        aria-describedby={`${id}-hint`}
        type="number"
        min={min}
        max={max}
        required
        disabled={value === null}
        className="disabled:bg-muted disabled:opacity-100 disabled:shadow-none dark:disabled:bg-muted"
        defaultValue={value ?? ""}
        onChange={(event) => {
          const next = event.target.valueAsNumber;
          const invalid = !Number.isFinite(next) || !event.target.validity.valid;
          onInvalidDraft(id, invalid);
          if (invalid) return;
          limited.current = next;
          onChange(next);
        }}
      />
      {fallback !== undefined && <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4 accent-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          aria-label={`${unsetLabel} ${label.toLowerCase()}`}
          checked={value === null}
          onChange={(event) => {
            onInvalidDraft(id, false);
            onChange(event.target.checked ? null : limited.current);
          }}
        />
        {unsetLabel}
      </label>}
    </div>
  </div>;
}

export function RepositorySettings({ repository: repo }: { repository: Repository }) {
  const connections = useApi<Connection[]>(
    `/connections?repository_id=${repo.id}`,
  );
  const action = useAction();
  const id = useId();
  const location = useLocation();
  const navigate = useNavigate();
  const contextGeneration = location.state?.contextGeneration as BackgroundHandoff | undefined;
  const generatedDraft = useRef(false);
  const [params, setParams] = useSearchParams();
  const sections = [
    { value: "reviews", label: "Pull request reviews", icon: GitPullRequest },
    { value: "scan-limits", label: "Security scans", icon: Shield },
    { value: "connection", label: "AI defaults", icon: Plug },
    { value: "context", label: "Project context", icon: FolderGit2 },
    { value: "code-graph", label: "Code graph", icon: Network },
  ];
  const tab = params.get("tab");
  const section = sections.find(({ value }) => value === tab)?.value ?? "reviews";
  const generation = params.get("generation");
  const reviewLimits = useRef<HTMLHeadingElement>(null);
  const invalidField = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (invalidField.current) focusInvalidField(invalidField.current);
    else if (tab === "limits") reviewLimits.current?.scrollIntoView();
    invalidField.current = null;
  }, [section, tab]);
  function sectionSearch(value: string) {
    const next = new URLSearchParams(params);
    next.set("tab", value);
    return next;
  }
  function focusInvalidField(field: HTMLElement) {
    field.focus({ preventScroll: true });
    field.scrollIntoView({ block: "center" });
  }
  function revealInvalidField(field: HTMLElement, target?: string) {
    if (target && target !== section) {
      invalidField.current = field;
      setParams(sectionSearch(target), { state: location.state });
    } else focusInvalidField(field);
  }
  const [enabled, setEnabled] = useState(repo.enabled);
  const [codeGraphEnabled, setCodeGraphEnabled] = useState(repo.code_graph_enabled ?? false);
  const savedGraphLimits = repo.code_graph_limits ?? { max_files: 1000, max_file_bytes: 1048576, max_source_bytes: 8388608 };
  const [graphDraft, setGraphDraft] = useState({
    max_files: String(savedGraphLimits.max_files),
    max_file_bytes: String(savedGraphLimits.max_file_bytes / 1048576),
    max_source_bytes: String(savedGraphLimits.max_source_bytes / 1048576),
  });
  const graphLimits = {
    max_files: Number(graphDraft.max_files),
    max_file_bytes: Number(graphDraft.max_file_bytes) * 1048576,
    max_source_bytes: Number(graphDraft.max_source_bytes) * 1048576,
  };
  const graphFileInput = useRef<HTMLInputElement>(null);
  useLayoutEffect(() => {
    graphFileInput.current?.setCustomValidity(graphLimits.max_file_bytes > graphLimits.max_source_bytes
      ? "Maximum individual file size cannot exceed Maximum total source size." : "");
  }, [graphLimits.max_file_bytes, graphLimits.max_source_bytes]);
  const [connection, setConnection] = useState(repo.connection_id ?? "");
  const savedBackground = useRef(repo.settings.background ?? "");
  const savedProjectSummary = useRef(repo.settings.project_summary ?? null);
  const expectedBackground = useRef<string>(undefined);
  const expectedProjectSummary = useRef<string | null>(undefined);
  const { rules: _legacyRules, ...editableSettings } = repo.settings;
  const [settings, setSettings] = useState({
    ...editableSettings,
    analysis_engine: "mortis" as const,
    timeout_seconds: repo.settings.timeout_seconds ?? null,
    token_budget: repo.settings.token_budget ?? null,
    assessment_token_budget: repo.settings.assessment_token_budget ?? null,
    verification_token_budget: repo.settings.verification_token_budget ?? null,
    summary_timeout_seconds: repo.settings.summary_timeout_seconds ?? null,
    summary_token_budget: repo.settings.summary_token_budget ?? null,
    max_files: repo.settings.max_files ?? null,
    background: repo.settings.background ?? "",
    project_summary: repo.settings.project_summary ?? null,
  });
  const sensitivityIndex = reviewSensitivities.findIndex((item) => item.value === (settings.review_sensitivity ?? "balanced"));
  const sensitivity = reviewSensitivities[sensitivityIndex];
  const [scanSettings, setScanSettings] = useState<Required<Schema["ScanSettings"]>>({
    token_budget: repo.scan_settings?.token_budget ?? null,
    timeout_seconds: repo.scan_settings?.timeout_seconds ?? null,
    concurrency: repo.scan_settings?.concurrency ?? 4,
    effort: repo.scan_settings?.effort ?? "high",
    exclusions: repo.scan_settings?.exclusions ?? "",
    max_output_tokens: repo.scan_settings?.max_output_tokens ?? null,
    assessment_token_budget: repo.scan_settings?.assessment_token_budget ?? null,
    verification_token_budget: repo.scan_settings?.verification_token_budget ?? null,
  });
  const savedScanSettings = useRef(scanSettings);
  const saveBar = useRef<HTMLElement>(null);
  const [invalidLimitDrafts, setInvalidLimitDrafts] = useState<ReadonlySet<string>>(() => new Set());
  const draft = { enabled, codeGraphEnabled, graphDraft, connection, settings, scanSettings };
  const [savedDraft, setSavedDraft] = useState(draft);
  const dirty = JSON.stringify(draft) !== JSON.stringify(savedDraft) || invalidLimitDrafts.size > 0;
  useEffect(() => {
    const background = repo.settings.background ?? "";
    const project_summary = repo.settings.project_summary ?? null;
    const previousBackground = savedBackground.current;
    const previousSummary = savedProjectSummary.current;
    if (background === previousBackground && project_summary === previousSummary) return;
    savedBackground.current = background;
    savedProjectSummary.current = project_summary;
    setSavedDraft((current) => ({ ...current, settings: { ...current.settings, background, project_summary } }));
    setSettings((current) => ({
      ...current,
      background: current.background === previousBackground ? background : current.background,
      project_summary: current.project_summary === previousSummary ? project_summary : current.project_summary,
    }));
  }, [repo.settings.background, repo.settings.project_summary]);
  const [error, setError] = useState<unknown>();
  const sharedConnections = connections.data?.filter((item) => item.kind === "api") ?? [];
  const selectedConnection = sharedConnections.find((item) => item.id === connection);
  function change<K extends keyof typeof settings>(
    key: K,
    value: (typeof settings)[K],
  ) {
    setSettings({ ...settings, [key]: value });
  }
  function trackInvalidLimitDraft(fieldId: string, invalid: boolean) {
    setInvalidLimitDrafts((current) => {
      if (current.has(fieldId) === invalid) return current;
      const next = new Set(current);
      if (invalid) next.add(fieldId);
      else next.delete(fieldId);
      return next;
    });
  }
  return (
    <div className="space-y-6">
      <PageTitle title="Repository settings" />
      <div className="grid gap-6 border-t pt-6 lg:grid-cols-[192px_minmax(0,1fr)] lg:gap-8">
        <SettingsNavigation label="Repository settings sections" value={section}
          items={sections.map((item) => ({ ...item, to: `?${sectionSearch(item.value)}`, state: location.state }))} />
        <form
          aria-label="Repository settings"
          className="min-w-0 max-w-2xl"
          onFocus={(event) => {
            const bar = saveBar.current;
            if (bar && !bar.contains(event.target)
              && event.target.getBoundingClientRect().bottom > bar.getBoundingClientRect().top) {
              event.target.scrollIntoView({ block: "center" });
            }
          }}
          onInvalidCapture={(event) => {
            event.preventDefault();
            const invalid = event.currentTarget.querySelector<HTMLInputElement>(":invalid");
            if (!invalid || invalid !== event.target) return;
            const field = invalid.getAttribute("aria-hidden") === "true"
              ? invalid.parentElement?.querySelector<HTMLInputElement>('input[role="combobox"]') ?? invalid
              : invalid;
            const target = invalid.closest<HTMLElement>("[data-settings-section]")?.dataset.settingsSection;
            setError(new Error(invalid.validationMessage));
            revealInvalidField(field, target);
          }}
          onSubmit={async (e) => {
            e.preventDefault();
            setError(undefined);
            const scanSettingsChanged = JSON.stringify(scanSettings) !== JSON.stringify(savedScanSettings.current);
            const { token_budget, timeout_seconds, concurrency, ...scanOptions } = scanSettings;
            const changedScanOptions = Object.fromEntries(Object.entries(scanOptions)
              .filter(([key, value]) => value !== savedScanSettings.current[key as keyof typeof scanOptions]));
            try {
              const { background, project_summary: summaryDraft, ...nextSettings } = settings;
              const project_summary = summaryDraft?.trim() || null;
              const contextChanged = savedBackground.current !== background;
              const summaryChanged = savedProjectSummary.current !== project_summary;
              await action.mutateAsync({
                path: `/repositories/${repo.id}`,
                method: "PUT",
                body: {
                  enabled,
                  code_graph_enabled: codeGraphEnabled,
                  code_graph_limits: graphLimits,
                  connection_id: connection || null,
                  ...(contextChanged ? { expected_background: expectedBackground.current ?? savedBackground.current } : {}),
                  ...(summaryChanged ? { expected_project_summary: expectedProjectSummary.current === undefined
                    ? savedProjectSummary.current : expectedProjectSummary.current } : {}),
                  ...(scanSettingsChanged ? { scan_settings: { token_budget, timeout_seconds, concurrency, ...changedScanOptions } } : {}),
                  settings: {
                    ...nextSettings,
                    ...(contextChanged ? { background } : {}),
                    ...(summaryChanged ? { project_summary } : {}),
                    concurrency: settings.concurrency,
                  },
                },
              });
              if (contextChanged) {
                savedBackground.current = background;
                expectedBackground.current = background;
              }
              if (summaryChanged) {
                savedProjectSummary.current = project_summary;
                expectedProjectSummary.current = project_summary;
              }
              if (contextGeneration && (contextChanged || summaryChanged || generatedDraft.current)) {
                const state = { ...location.state };
                delete state.contextGeneration;
                navigate(`${location.pathname}${location.search}${location.hash}`, { replace: true, state });
              }
              if (scanSettingsChanged) savedScanSettings.current = scanSettings;
              setSavedDraft(draft);
              toast.success("Repository settings saved.", { position: "top-right" });
            } catch (e) {
              setError(e);
            }
          }}
        >
          <section hidden={section !== "reviews"} data-settings-section="reviews" aria-labelledby={`${id}-reviews`} className="space-y-6">
            <div>
              <h2 id={`${id}-reviews`} className="text-xl font-semibold tracking-tight">Pull request reviews</h2>
              <p className="mt-1 text-sm text-muted-foreground">Configure when reviews run, what they report, and their execution limits.</p>
              <a href={`#${id}-review-limits`} className="mt-2 inline-block rounded-sm text-sm underline underline-offset-4"
                onClick={(event) => {
                  event.preventDefault();
                  reviewLimits.current?.focus({ preventScroll: true });
                  reviewLimits.current?.scrollIntoView();
                }}>Jump to run limits</a>
            </div>
            <section aria-labelledby={`${id}-review-triggers`} className="space-y-3">
              <h3 id={`${id}-review-triggers`} className="text-base font-semibold">When reviews run</h3>
              <div className="divide-y rounded-lg border bg-card">
                <div className="flex items-start justify-between gap-4 p-5">
                  <div className="space-y-2">
                    <label htmlFor={`${id}-enabled`} className="text-sm font-medium">Enable pull request reviews</label>
                    <p id={`${id}-enabled-hint`} className="text-xs leading-relaxed text-muted-foreground">Allow reviews for this repository using the selected AI connection.</p>
                  </div>
                  <Switch id={`${id}-enabled`} aria-describedby={`${id}-enabled-hint`} checked={enabled} onCheckedChange={setEnabled} className="mt-0.5 shrink-0" />
                </div>
                <div className="flex items-start justify-between gap-4 p-5">
                  <div className="space-y-2">
                    <label htmlFor={`${id}-automatic`} className="text-sm font-medium">Automatically review new commits</label>
                    <p id={`${id}-automatic-hint`} className="text-xs leading-relaxed text-muted-foreground">Reviews new commits on open, non-draft pull requests. Forks require a manual start.</p>
                  </div>
                  <Switch id={`${id}-automatic`} aria-describedby={`${id}-automatic-hint`} checked={settings.automatic} onCheckedChange={(value) => change("automatic", value)} className="mt-0.5 shrink-0" />
                </div>
                <div className="flex items-start justify-between gap-4 p-5">
                  <div className="space-y-2">
                    <label htmlFor={`${id}-bots`} className="text-sm font-medium">Include bot pull requests</label>
                    <p id={`${id}-bots-hint`} className="text-xs leading-relaxed text-muted-foreground">Import pull requests from bots such as Dependabot and apply your automatic review settings. Existing pull requests stay visible when this is off.</p>
                  </div>
                  <Switch id={`${id}-bots`} aria-describedby={`${id}-bots-hint`} checked={settings.include_bot_prs ?? false} onCheckedChange={(value) => change("include_bot_prs", value)} className="mt-0.5 shrink-0" />
                </div>
              </div>
            </section>
            <section aria-labelledby={`${id}-review-behavior`} className="space-y-3">
              <h3 id={`${id}-review-behavior`} className="text-base font-semibold">Review behavior</h3>
              <div className="divide-y rounded-lg border bg-card">
                <div className="space-y-2 p-5">
                  <label htmlFor={`${id}-sensitivity`} className="text-sm font-medium">Finding sensitivity</label>
                  <input
                    id={`${id}-sensitivity`}
                    type="range"
                    min={0}
                    max={2}
                    step={1}
                    value={sensitivityIndex}
                    aria-valuetext={sensitivity.label}
                    aria-describedby={`${id}-sensitivity-hint`}
                    onChange={(event) => change("review_sensitivity", reviewSensitivities[Number(event.target.value)].value)}
                    className="block h-11 w-full cursor-pointer rounded-sm accent-primary"
                  />
                  <div aria-hidden="true" className="grid grid-cols-3 gap-3 text-xs">
                    {reviewSensitivities.map((item) => <span key={item.value}
                      className={`text-center first:text-left last:text-right ${item.value === sensitivity.value ? "font-medium text-foreground" : "text-muted-foreground"}`}>
                      {item.label}
                    </span>)}
                  </div>
                  <p id={`${id}-sensitivity-hint`} className="text-xs leading-relaxed text-muted-foreground">{sensitivity.description}</p>
                </div>
                <div className="p-5">
                  <Field label="Review effort" hint={<span id={`${id}-review-effort-hint`}>
                    Higher effort lets the review gather more evidence, which can take longer and use more tokens.
                  </span>}>
                    {(id) => (
                      <SearchSelect
                        id={id}
                        aria-describedby={`${id}-review-effort-hint`}
                        required
                        value={settings.effort}
                        onChange={(value) =>
                          change("effort", value as "low" | "medium" | "high")
                        }
                        options={["low", "medium", "high"].map((value) => ({ value, label: value }))}
                        placeholder="Choose review effort"
                      />
                    )}
                  </Field>
                </div>
                <div className="flex items-start justify-between gap-4 p-5">
                  <div className="space-y-2">
                    <label htmlFor={`${id}-resolve`} className="text-sm font-medium">Automatically resolve verified fixes</label>
                    <p id={`${id}-resolve-hint`} className="text-xs leading-relaxed text-muted-foreground">Verifies earlier findings in manual and automatic reviews. Requires GitHub App Contents: Read and write. Uses the verification budget; uncertain findings stay open.</p>
                  </div>
                  <Switch id={`${id}-resolve`} aria-describedby={`${id}-resolve-hint`} checked={settings.auto_resolve ?? false} onCheckedChange={(value) => change("auto_resolve", value)} className="mt-0.5 shrink-0" />
                </div>
              </div>
            </section>
            <section aria-labelledby={`${id}-review-output`} className="space-y-3">
              <h3 id={`${id}-review-output`} className="text-base font-semibold">GitHub output</h3>
              <div className="divide-y rounded-lg border bg-card">
                <div className="p-5">
                  <Field label="Review language">
                    {(id) => (
                      <SearchSelect
                        id={id}
                        required
                        value={settings.language}
                        onChange={(value) => change("language", value)}
                        options={languages.map((value) => ({ value, label: value }))}
                        placeholder="Choose or enter a language"
                        allowCustom
                      />
                    )}
                  </Field>
                </div>
                {([
                  ["summary_diagram_enabled", "Include a diagram in the PR overview", "Include a collapsed diagram in the PR overview, generated with the summary."],
                  ["ai_fix_prompts_enabled", "Include AI fix prompts", "Include fix prompts in the PR overview and new finding comments."],
                ] as const).map(([key, label, hint]) => <div key={key} className="flex items-start justify-between gap-4 p-5">
                  <div className="space-y-2">
                    <label htmlFor={`${id}-${key}`} className="text-sm font-medium">{label}</label>
                    <p id={`${id}-${key}-hint`} className="text-xs leading-relaxed text-muted-foreground">{hint}</p>
                  </div>
                  <Switch id={`${id}-${key}`} aria-describedby={`${id}-${key}-hint`} checked={settings[key] ?? false} onCheckedChange={(value) => change(key, value)} className="mt-0.5 shrink-0" />
                </div>)}
              </div>
            </section>
            <section aria-labelledby={`${id}-review-limits`} className="space-y-3">
              <h3 ref={reviewLimits} id={`${id}-review-limits`} tabIndex={-1} className="scroll-mt-32 rounded-sm text-base font-semibold sm:scroll-mt-18">Run limits</h3>
              {([
                { key: "execution", title: "Execution", fields: [
                  { key: "max_files", label: "Maximum reviewable files", min: 1, max: undefined, fallback: 40,
                    hint: "Skips the entire review when reviewable files exceed this limit. Excluded files do not count." },
                  { key: "timeout_seconds", label: "Time limit (seconds)", min: 60, max: 3600, fallback: 900,
                    hint: "Applies to reviews and background generation. Unlimited removes the deadline. Provider and connection timeouts still apply." },
                ] },
                { key: "budgets", title: "Token budgets", fields: [
                  { key: "token_budget", label: "Analysis token budget", min: 1000, max: 5000000, fallback: 200000,
                    hint: "Budget for Mortis analysis. Also supplies the initial background generation limit. Provider and account limits still apply." },
                  { key: "assessment_token_budget", label: "Assessment token budget", min: 1000, max: 5000000, fallback: 200000,
                    hint: "Independent budget for validating proposed findings, including correction attempts." },
                  { key: "verification_token_budget", label: "Verification token budget", min: 1000, max: 5000000, fallback: 200000,
                    hint: "Independent budget for checking whether earlier findings were fixed." },
                ] },
                { key: "summary", title: "PR summary", fields: [
                  { key: "summary_timeout_seconds", label: "Summary time limit (seconds)", min: 60, max: 3600, fallback: 180,
                    hint: "Separate time allowance for the PR summary, including when the file limit skips detailed review." },
                  { key: "summary_token_budget", label: "Summary token budget", min: 1000, max: 5000000, fallback: 60000,
                    hint: "Adds input and output token usage beyond the review budget. Provider accounting applies; this is not a guaranteed spending cap." },
                ] },
              ] as const).map((group) => (
                <section key={group.key} aria-labelledby={`${id}-${group.key}`} className="space-y-3">
                  <h4 id={`${id}-${group.key}`} className="text-sm font-semibold">{group.title}</h4>
                  <div className="divide-y rounded-lg border bg-card">
                    {group.fields.map(({ key, ...field }) => <ExecutionLimitField key={key} id={`${id}-${key}`}
                      {...field} value={settings[key]} onChange={(value) => change(key, value)} onInvalidDraft={trackInvalidLimitDraft} />)}
                    {group.key === "execution" && <ExecutionLimitField id={`${id}-concurrency`} label="Parallel review tasks"
                      hint="Maximum number of review tasks running in parallel. Lower values reduce simultaneous requests to your AI provider."
                      min={1} max={8} value={settings.concurrency}
                      onChange={(value) => { if (value !== null) change("concurrency", value); }} onInvalidDraft={trackInvalidLimitDraft} />}
                  </div>
                </section>
              ))}
            </section>
          </section>
          <section hidden={section !== "scan-limits"} data-settings-section="scan-limits" aria-labelledby={`${id}-scan-limits`} className="space-y-6">
            <div>
              <h2 id={`${id}-scan-limits`} className="text-xl font-semibold tracking-tight">Security scans</h2>
              <p className="mt-1 text-sm text-muted-foreground">Configure AI analysis and execution limits for new scans of this repository.</p>
            </div>
            <section aria-labelledby={`${id}-scan-execution`} className="space-y-3">
              <h3 id={`${id}-scan-execution`} className="text-base font-semibold">Run limits</h3>
              <div className="divide-y rounded-lg border bg-card">
                <ExecutionLimitField id={`${id}-scan-timeout`} label="Scan time limit (seconds)"
                  hint="Time available for the complete scan. Unlimited removes the execution deadline."
                  min={60} max={3600} fallback={900} value={scanSettings.timeout_seconds}
                  onChange={(timeout_seconds) => setScanSettings((current) => ({ ...current, timeout_seconds }))} onInvalidDraft={trackInvalidLimitDraft} />
                <ExecutionLimitField id={`${id}-scan-concurrency`} label="Parallel scan tasks"
                  hint="Maximum number of scan tasks running in parallel. All tasks share the scan budget."
                  min={1} max={8} value={scanSettings.concurrency}
                  onChange={(concurrency) => { if (concurrency !== null) setScanSettings((current) => ({ ...current, concurrency })); }} onInvalidDraft={trackInvalidLimitDraft} />
                <ExecutionLimitField id={`${id}-scan-token-budget`} label="Total scan token budget"
                  hint="Total budget shared by scan analysis, assessment, verification, and correction attempts."
                  min={1000} max={5000000} fallback={200000} value={scanSettings.token_budget}
                  onChange={(token_budget) => setScanSettings((current) => ({ ...current, token_budget }))} onInvalidDraft={trackInvalidLimitDraft} />
                {([
                  { key: "max_output_tokens", label: "Maximum output tokens per response",
                    hint: "Automatic uses the available scan budget and provider limits.", min: 1, max: 1000000,
                    fallback: 16384, unsetLabel: "Automatic" },
                  { key: "assessment_token_budget", label: "Scan assessment token budget",
                    hint: "Caps AI assessment and its correction attempts within the total scan budget.", min: 1000,
                    max: 5000000, fallback: 200000, unsetLabel: "No separate cap" },
                  { key: "verification_token_budget", label: "Scan verification token budget",
                    hint: "Caps AI fix verification and its correction attempts within the total scan budget.", min: 1000,
                    max: 5000000, fallback: 200000, unsetLabel: "No separate cap" },
                ] as const).map(({ key, ...field }) => <ExecutionLimitField key={key} id={`${id}-scan-${key}`}
                  {...field} value={scanSettings[key]}
                  onChange={(value) => setScanSettings((current) => ({ ...current, [key]: value }))} onInvalidDraft={trackInvalidLimitDraft} />)}
              </div>
            </section>
            <section aria-labelledby={`${id}-scan-policy`} className="space-y-3">
              <h3 id={`${id}-scan-policy`} className="text-base font-semibold">What scans check</h3>
              <div className="divide-y rounded-lg border bg-card">
                <div className="p-5">
                  <Field label="Scan reasoning effort" hint="Higher effort can take longer and use more tokens.">
                    {(fieldId) => <SearchSelect id={fieldId} required value={scanSettings.effort}
                      onChange={(value) => setScanSettings((current) => ({ ...current, effort: value as typeof current.effort }))}
                      options={[{ value: "low", label: "Low" }, { value: "medium", label: "Medium" }, { value: "high", label: "High" }]} />}
                  </Field>
                </div>
                <div className="p-5">
                  <Field label="AI path exclusions"
                    hint="Separate glob patterns with commas or new lines, for example fixtures/**. Patterns only exclude files. Trivy still checks eligible files.">
                    {(fieldId) => <Textarea id={fieldId} rows={3} maxLength={4000} value={scanSettings.exclusions}
                      onChange={(event) => {
                        const input = event.currentTarget;
                        input.setCustomValidity(input.value.split(/[,\n]/).some((pattern) => pattern.trim().startsWith("!"))
                          ? "Inclusion overrides are not allowed." : "");
                        trackInvalidLimitDraft(fieldId, !input.validity.valid);
                        setScanSettings((current) => ({ ...current, exclusions: input.value }));
                      }} />}
                  </Field>
                </div>
              </div>
            </section>
          </section>
          <section hidden={section !== "connection"} data-settings-section="connection" aria-labelledby={`${id}-connection`} className="space-y-6">
            <div>
              <h2 id={`${id}-connection`} className="text-xl font-semibold tracking-tight">AI defaults</h2>
              <p className="mt-1 text-sm text-muted-foreground">Pull request reviews use these settings by default. They’re also prefilled when starting a security scan, where you can choose a different connection or model.</p>
            </div>
            {repo.connection_requires_setup && <p role="status" className="rounded-md border p-4 text-sm">The saved subscription connection is retired. Select a shared API connection to resume reviews. Historical runs remain available.</p>}
            <Field label="Default AI connection">
              {(id) => (
                <SearchSelect
                  id={id}
                  value={connection}
                  disabled={connections.isPending}
                  required={enabled}
                  placeholder={connections.isPending ? "Loading connections…" : "Choose a connection"}
                  onChange={(value) => {
                    setConnection(value);
                    change("model", "");
                  }}
                  options={[
                    { value: "", label: "No connection" },
                    ...(connection && !selectedConnection ? [{ value: connection, label: connections.isPending ? "Loading connections…" : repo.connection_requires_setup ? "Retired connection" : "Connection unavailable" }] : []),
                    ...sharedConnections.map((item) => ({ value: item.id, label: item.name })),
                  ]}
                />
              )}
            </Field>
            <ModelPicker
              key={connection}
              label="Model override (optional)"
              value={settings.model}
              onChange={(value) => change("model", value)}
              path={connection ? `/connections/${connection}/models?repository_id=${repo.id}` : undefined}
              enabled={!!selectedConnection}
              emptyLabel={selectedConnection?.model || "Use connection model"}
              hint="Choose an AI connection to load its models."
            />
          </section>
          <section hidden={section !== "context"} data-settings-section="context" aria-labelledby={`${id}-context`} className="space-y-6">
            <div>
              <h2 id={`${id}-context`} className="text-xl font-semibold tracking-tight">Project context</h2>
              <p className="mt-1 text-sm text-muted-foreground">Give reviewers context and choose which files to exclude.</p>
            </div>
            {section === "context" && generation && <GenerationActivity repositoryId={repo.id} jobId={generation} />}
            <BackgroundField
              repositoryId={repo.id}
              connectionId={connection}
              model={settings.model}
              defaultModel={selectedConnection?.model ?? ""}
              tokenBudget={settings.token_budget}
              timeoutSeconds={settings.timeout_seconds}
              value={{ background: settings.background, project_summary: settings.project_summary }}
              onChange={(value, generated) => {
                if (generated) generatedDraft.current = true;
                if (value.background !== undefined && settings.background === savedBackground.current) {
                  expectedBackground.current = savedBackground.current;
                }
                if (value.project_summary !== undefined && settings.project_summary === savedProjectSummary.current) {
                  expectedProjectSummary.current = savedProjectSummary.current;
                }
                setSettings((current) => ({ ...current, ...value }));
              }}
              initialJobId={contextGeneration?.jobId}
              initialGenerationId={contextGeneration?.generationId}
              initialSummaryDraft={contextGeneration?.mode === "summary_draft"}
              initialContextChanged={!!contextGeneration && contextGeneration.inputs !== savedBackgroundInputs(repo)}
            />
            <Field
              label="Additional file exclusions"
              hint="Comma-separated glob patterns, such as fixtures/**. These add to the mandatory policy for reviews, context, and code graph indexing. Inclusion overrides are not allowed."
            >
              {(id) => (
                <Input
                  id={id}
                  value={settings.exclusions}
                  onChange={(e) => change("exclusions", e.target.value)}
                />
              )}
            </Field>
            <details className="text-sm">
              <summary className="cursor-pointer font-medium">Mandatory file exclusions</summary>
              <div className="mt-3 space-y-2 text-muted-foreground">
                <p>Always excluded from reviews, context, and the code graph. These exclusions cannot be disabled.</p>
                <ul className="list-disc space-y-1 pl-5">
                  <li>Images, including SVG; audio and video; fonts.</li>
                  <li>Archives and binary documents, including PDF and Office files.</li>
                  <li>Compiled artifacts, dependency, build and cache directories.</li>
                  <li>Source maps, minified JavaScript/CSS, and package-manager lockfiles.</li>
                  <li>Secrets, credentials, agent instructions, and unsafe paths.</li>
                </ul>
                <p>Markdown, MDX, text documentation, source, and configuration files remain eligible. Security scans use their own coverage policy.</p>
              </div>
            </details>
          </section>
          <section hidden={section !== "code-graph"} data-settings-section="code-graph" aria-labelledby={`${id}-code-graph`} className="space-y-6">
            <div>
              <h2 id={`${id}-code-graph`} className="text-xl font-semibold tracking-tight">Code graph</h2>
              <p className="mt-1 text-sm text-muted-foreground">Manage indexing, source limits, and the current index.</p>
            </div>
            <CodeGraphSettings repositoryId={repo.id} enabled={codeGraphEnabled} savedEnabled={repo.code_graph_enabled ?? false}
              visible={section === "code-graph"} onChange={setCodeGraphEnabled}
              dirty={codeGraphEnabled !== (repo.code_graph_enabled ?? false) || settings.exclusions !== repo.settings.exclusions
                || Object.entries(graphLimits).some(([key, value]) => value !== savedGraphLimits[key as keyof typeof savedGraphLimits])} />
            <section aria-labelledby={`${id}-indexing-limits`} className="space-y-4">
              <h3 id={`${id}-indexing-limits`} className="text-base font-semibold">Indexing limits</h3>
              <p className="text-sm text-muted-foreground">Limits apply to eligible source after mandatory and additional exclusions and language filtering. Unsupported formats retain metadata for coverage and source navigation; their bodies are not parsed.</p>
              <div className="divide-y rounded-lg border bg-card">
                {([
                  { key: "max_files", label: "Maximum indexed files", max: 5000, min: 1, step: 1, hint: "Default: 1,000. Maximum: 5,000 files." },
                  { key: "max_file_bytes", label: "Maximum individual file size (MiB)", max: 4, min: 1 / 1048576, step: 1 / 1048576, hint: "Default: 1 MiB. Maximum: 4 MiB. Larger files are skipped with a warning." },
                  { key: "max_source_bytes", label: "Maximum total source size (MiB)", max: 32, min: 1 / 1048576, step: 1 / 1048576, hint: "Default: 8 MiB. Maximum: 32 MiB. Exceeding this limit or the file count fails the attempt and preserves the previous index." },
                ] as const).map((field) => {
                  const fieldId = `${id}-indexing-${field.key}`;
                  return <div key={field.key} className="grid gap-4 p-5 sm:grid-cols-[minmax(0,1fr)_160px] sm:gap-6">
                    <div className="space-y-2">
                      <label htmlFor={fieldId} className="text-sm font-medium">{field.label}</label>
                      <p id={`${fieldId}-hint`} className="text-xs leading-relaxed text-muted-foreground">{field.hint}</p>
                    </div>
                    <Input id={fieldId} aria-describedby={`${fieldId}-hint`} type="number" required min={field.min} max={field.max} step={field.step}
                      ref={field.key === "max_file_bytes" ? graphFileInput : undefined}
                      value={graphDraft[field.key]}
                      onChange={(event) => { setError(undefined); setGraphDraft({ ...graphDraft, [field.key]: event.target.value }); }} />
                  </div>;
                })}
              </div>
              <p className="text-xs text-muted-foreground">Saving changes schedules a fresh index when enabled. Paused indexing stays paused until you retry.</p>
            </section>
          </section>
          <footer ref={saveBar} className="sticky bottom-0 z-10 mt-8 space-y-3 bg-background py-4">
            {!!(connections.error || error) && <div role="region" aria-label="Settings errors" tabIndex={0}
              className="max-h-[25dvh] space-y-3 overflow-y-auto rounded-md">
              <ErrorNotice error={connections.error} />
              <ErrorNotice error={error} />
            </div>}
            <div className="flex flex-wrap items-center justify-end gap-2">
              <p role="status" className="mr-auto text-sm text-muted-foreground empty:sr-only">{dirty ? "Unsaved changes" : ""}</p>
              <Button type="submit" disabled={action.isPending}>
                {action.isPending ? "Saving…" : "Save"}
              </Button>
            </div>
          </footer>
        </form>
      </div>
    </div>
  );
}

function ConnectionForm({
  connection,
  providers,
  onSaved,
  onSavingChange,
}: {
  connection?: Connection;
  providers: Provider[];
  onSaved: (connection: Connection) => void;
  onSavingChange: (saving: boolean) => void;
}) {
  const kind = "api";
  const tabbed = !!connection && kind === "api";
  const [tab, setTab] = useState("general");
  const advancedSettings = useRef<HTMLDetailsElement>(null);
  const first = providers.find((p) => p.name === "openai") ?? providers[0];
  const [name, setName] = useState(connection?.name ?? "");
  const [provider, setProvider] = useState(
    connection?.provider ?? first?.name ?? "openai",
  );
  const [protocol, setProtocol] = useState(
    connection?.protocol ?? first?.protocol ?? "openai-responses",
  );
  const [url, setUrl] = useState(
    connection?.base_url ?? first?.baseUrl ?? "https://api.openai.com/v1",
  );
  const [model, setModel] = useState(connection?.model ?? "");
  const [key, setKey] = useState("");
  const [headers, setHeaders] = useState("");
  const { prompt_token_ceiling: savedCeiling, ...providerOptions } = connection?.options ?? {};
  const [promptCeiling, setPromptCeiling] = useState(String(savedCeiling ?? 58888));
  const [options, setOptions] = useState(
    JSON.stringify(providerOptions, null, 2),
  );
  const [error, setError] = useState<unknown>();
  const action = useAction<Connection>();
  // Draft credentials stay in component memory, never in query keys or browser storage.
  const context = JSON.stringify([kind, provider, protocol, url, key, headers, options]);
  const initialContext = useRef(context);
  const customEndpoint = provider === "custom";
  function promptTokenCeiling() {
    const ceiling = Number(promptCeiling);
    if (!Number.isInteger(ceiling) || ceiling < 4096 || ceiling > 2000000) {
      throw new Error(`${kind === "api" ? "Evidence prompt" : "Prompt"} ceiling must be an integer between 4096 and 2000000.`);
    }
    return ceiling;
  }
  function apiBody() {
    const ceiling = promptTokenCeiling();
    return {
      kind, provider, protocol, base_url: url,
      ...(key ? { api_key: key } : {}),
      ...(headers.trim() ? { extra_headers: parseObject(headers, tabbed ? "Extra headers JSON in the Advanced tab" : "Headers") } : {}),
      options: { ...parseObject(options, tabbed ? "Provider options JSON in the Advanced tab" : "Options"), prompt_token_ceiling: ceiling },
    };
  }
  function choose(value: string) {
    const preset = providers.find((p) => p.name === value);
    setProvider(value);
    if (value !== provider) {
      setModel("");
      setKey("");
      setHeaders("");
    }
    if (preset) {
      setProtocol(preset.protocol);
      setUrl(preset.baseUrl);
    }
  }
  const endpointFields = <>
    <Field label="API protocol">
      {(id) => <SearchSelect id={id} value={protocol} onChange={setProtocol}
        options={protocols.map((value) => ({ value, label: value }))} />}
    </Field>
    {protocol !== "anthropic-bedrock" && <Field label="API base URL">
      {(id) => <Input id={id} required type="url" value={url} onChange={(event) => setUrl(event.target.value)} />}
    </Field>}
  </>;
  const nameField = <Field label="Connection name">
    {(id) => <Input id={id} required maxLength={100} value={name}
      onChange={(event) => setName(event.target.value)} placeholder="Team review model" />}
  </Field>;
  const ceilingField = <Field
    label="Evidence prompt ceiling (tokens)"
    hint="Limits connection tests and applies to older saved jobs. Set limits for new reviews and scans in Repository settings."
  >
    {(id) => <Input id={id} required type="number" min={4096} max={2000000} step={1}
      value={promptCeiling} onChange={(event) => setPromptCeiling(event.target.value)} />}
  </Field>;
  const authentication = <section className={tabbed ? "space-y-4" : "space-y-4 border-t pt-5"} aria-label="Provider and authentication">
    <h3 className="text-sm font-semibold">Provider and authentication</h3>
    <Field label="Provider">
      {(id) => <SearchSelect id={id} value={provider} onChange={choose}
        options={providers.map((p) => {
          const logo = apiProviderLogos.get(p.name);
          return { value: p.name, label: p.displayName, icon: logo ? <ProviderLogo logo={logo} /> : <Plug aria-hidden="true" className="size-4 shrink-0 text-primary" /> };
        })} />}
    </Field>
    {(customEndpoint || tabbed) && endpointFields}
    <Field label={connection?.has_key ? "Replace API key (optional)" : "API key"}
      hint={protocol === "anthropic-bedrock"
        ? "Bedrock can use the worker’s AWS credentials."
        : connection?.has_key
          ? "Leave blank to keep the stored key. Keys are encrypted and never returned to the browser."
          : customEndpoint
            ? "For a trusted local endpoint without authentication, enter a placeholder."
            : "Keys are encrypted and never returned to the browser."}
    >
      {(id) => <Input id={id} type="password" autoComplete="new-password"
        required={!connection?.has_key && protocol !== "anthropic-bedrock"}
        value={key} onChange={(event) => setKey(event.target.value)} />}
    </Field>
  </section>;
  const modelFields = <section className={tabbed ? "space-y-4" : "space-y-4 border-t pt-5"} aria-label="Default model">
    <h3 className="text-sm font-semibold">Default model</h3>
    <ModelPicker value={model} onChange={setModel} required context={context}
      path={connection && context === initialContext.current ? `/connections/${connection.id}/models` : undefined}
      loadDraft={() => api<Schema["ModelCatalog"]>("/connections/models", "POST", {
        ...apiBody(), ...(connection ? { connection_id: connection.id } : {}),
      })} />
  </section>;
  const advancedFields = <>
    {!customEndpoint && !tabbed && endpointFields}
    {!tabbed && ceilingField}
    <Field label="Extra headers JSON"
      hint={connection?.header_names?.length
        ? `Stored headers: ${connection.header_names!.join(", ")}. Leave blank to keep them; use {} to clear.`
        : "Values are encrypted alongside the API key."}
    >
      {(id) => <Textarea id={id} rows={3} className="font-mono text-xs" value={headers}
        onChange={(event) => setHeaders(event.target.value)} placeholder={'{"X-Custom-Header": "value"}'} />}
    </Field>
    <Field label="Provider options JSON" hint="Supports extra_body, retry_codes, aws_region, aws_profile, and auth_header. timeout_sec applies to connection tests and older saved jobs.">
      {(id) => <Textarea id={id} rows={4} className="font-mono text-xs" value={options}
        onChange={(event) => setOptions(event.target.value)} />}
    </Field>
  </>;
  return (
    <form className={tabbed ? "flex min-h-0 flex-1 flex-col gap-5" : "space-y-5"} noValidate={tabbed}
      onSubmit={async (event) => {
        event.preventDefault();
        if (action.isPending) return;
        if (tabbed) {
          const invalid = event.currentTarget.querySelector<HTMLInputElement | HTMLTextAreaElement>("input:invalid, textarea:invalid");
          if (invalid) {
            setTab(invalid.closest<HTMLElement>("[data-connection-tab]")!.dataset.connectionTab!);
            requestAnimationFrame(() => { invalid.focus(); invalid.reportValidity(); });
            return;
          }
        }
        let body;
        try {
          body = { name, model, ...apiBody() };
        } catch (error) {
          if (tabbed) setTab("advanced");
          else if (advancedSettings.current) {
            advancedSettings.current.open = true;
            requestAnimationFrame(() => {
              const summary = advancedSettings.current?.querySelector("summary");
              summary?.focus();
              summary?.scrollIntoView({ block: "center" });
            });
          }
          setError(error);
          return;
        }
        onSavingChange(true);
        setError(undefined);
        try {
          const saved = await action.mutateAsync({
            path: connection ? `/connections/${connection.id}` : "/connections",
            method: connection ? "PUT" : "POST", body,
          });
          toast.success(connection ? "Connection saved." : "Connection created.");
          onSaved(saved);
        } catch (error) {
          setError(error);
        } finally {
          onSavingChange(false);
        }
      }}
    >
      {tabbed ? <Tabs value={tab} onValueChange={setTab} className="min-h-0 flex-1 gap-5">
        <TabsList className="grid w-full shrink-0 grid-cols-3" aria-label="Shared connection settings">
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="connection">Connection</TabsTrigger>
          <TabsTrigger value="advanced">Advanced</TabsTrigger>
        </TabsList>
        <TabsContent value="general" data-connection-tab="general" forceMount
          className="-m-1 min-h-0 space-y-4 overflow-x-hidden overflow-y-auto p-1 data-[state=inactive]:hidden">
          <ErrorNotice error={error} />
          {nameField}{modelFields}{ceilingField}
        </TabsContent>
        <TabsContent value="connection" data-connection-tab="connection" forceMount
          className="-m-1 min-h-0 space-y-4 overflow-x-hidden overflow-y-auto p-1 data-[state=inactive]:hidden">
          <ErrorNotice error={error} />
          {authentication}
        </TabsContent>
        <TabsContent value="advanced" data-connection-tab="advanced" forceMount
          className="-m-1 min-h-0 space-y-4 overflow-x-hidden overflow-y-auto p-1 data-[state=inactive]:hidden">
          <ErrorNotice error={error} />
          {advancedFields}
        </TabsContent>
      </Tabs> : <>
        <ErrorNotice error={error} />
        <section className="space-y-4" aria-label="Connection details">
          <h3 className="text-sm font-semibold">Connection details</h3>
          {nameField}
        </section>
        {authentication}{modelFields}
        {kind === "api" && <details ref={advancedSettings} className="border-t pt-5">
          <summary className="cursor-pointer text-sm font-medium">Advanced provider settings</summary>
          <div className="mt-4 space-y-4">{advancedFields}</div>
        </details>}
      </>}
      {tabbed ? <DialogFooter className="shrink-0 border-t pt-4">
        <Button disabled={action.isPending} type="submit">{action.isPending ? "Saving…" : "Save connection"}</Button>
      </DialogFooter> : <div className="sticky bottom-0 border-t bg-background py-4">
        <Button disabled={action.isPending} type="submit">
          {action.isPending ? "Saving…" : kind !== "api" && !connection ? "Continue to sign in" : "Save connection"}
        </Button>
      </div>}
    </form>
  );
}

export function SettingsPage({ user, mode, theme, dark, onModeChange, onThemeChange, creatingToken = false }: {
  user: Schema["UserOut"];
  mode: ColorMode;
  theme: ThemeId;
  dark: boolean;
  onModeChange: (mode: ColorMode) => void;
  onThemeChange: (theme: ThemeId) => void;
  creatingToken?: boolean;
}) {
  const isAdmin = user.role === "admin";
  const [params] = useSearchParams();
  const sections = [
    { value: "connections", label: "AI connections", icon: Plug },
    { value: "api-tokens", label: "API tokens", icon: KeyRound },
    { value: "mcp", label: "MCP access", icon: Bot },
    ...(isAdmin ? [
      { value: "github", label: "GitHub", icon: Github },
      { value: "team", label: "Team", icon: Users },
    ] : []),
    { value: "appearance", label: "Appearance", icon: Palette },
  ];
  const tab = creatingToken ? "api-tokens" : sections.some(({ value }) => value === params.get("tab"))
    ? params.get("tab")! : "connections";
  const query = useApi<Instance>("/settings", true, isAdmin && tab !== "appearance");
  const connections = useApi<Connection[]>("/connections", true, tab === "connections");
  const providers = useApi<Provider[]>("/providers", false, tab === "connections");
  const action = useAction();
  const memberAction = useAction();
  const [editing, setEditing] = useState<Connection | "new" | null>(null);
  const [savingConnection, setSavingConnection] = useState(false);
  const [deleting, setDeleting] = useState<Connection | null>(null);
  const [member, setMember] = useState<Instance["members"][number] | "new" | null>(null);
  const [removingMember, setRemovingMember] = useState<Instance["members"][number] | null>(null);
  const [login, setLogin] = useState("");
  const [role, setRole] = useState("member");
  const [webhookCopy, setWebhookCopy] = useState<"copied" | "failed" | null>(null);
  const memberSubmitLabel = member === "new" ? "Add or update member" : "Save role";
  const returnFocus = useRef<HTMLElement | null>(null);
  const connectionHeading = useRef<HTMLHeadingElement>(null);
  const memberHeading = useRef<HTMLHeadingElement>(null);
  const instance = query.data;
  const administrators = instance?.members.filter((item) => item.role === "admin") ?? [];
  const lastAdministrator = administrators.length === 1 ? administrators[0].id : undefined;
  const editedMember = member === "new" ? instance?.members.find((item) => item.login.toLowerCase() === login.toLowerCase()) : member;
  const memberRoleLocked = editedMember?.role === "admin" && (editedMember.id === user.id || editedMember.id === lastAdministrator);
  const webhookEndpoint = instance?.webhook_url ?? `${instance?.public_url}/api/v1/webhooks/github`;
  const groups = [
    { title: "Shared API connections", kind: "api", items: connections.data?.filter((item) => (item.kind ?? "api") === "api") ?? [] },
  ];

  function openMember(value: Instance["members"][number] | "new") {
    if (memberAction.isPending) return;
    memberAction.reset();
    setLogin(value === "new" ? "" : value.login);
    setRole(value === "new" ? "member" : value.role);
    setMember(value);
  }
  function restoreFocus(event: Event) {
    event.preventDefault();
    returnFocus.current?.focus();
  }

  return <>
    <PageTitle title="Settings">
        {isAdmin ? "Manage AI connections, API tokens, MCP access, GitHub installations, team access, and appearance." : "View shared AI connections and manage your API tokens, MCP access, and appearance."}
    </PageTitle>
    <div className="grid min-w-0 gap-6 border-t pt-6 lg:grid-cols-[192px_minmax(0,1fr)] lg:gap-8">
      <SettingsNavigation label="Settings sections" value={tab}
        items={sections.map((section) => ({ ...section, to: `/settings?tab=${section.value}` }))} />
      <div className="settings-content min-w-0 space-y-6">
        {tab !== "appearance" && <ErrorNotice error={query.error} />}
        {tab === "appearance" && <AppearanceSettings mode={mode} theme={theme} dark={dark} onModeChange={onModeChange} onThemeChange={onThemeChange} />}
        {tab === "api-tokens" && (creatingToken ? <CreateApiTokenPage /> : <ApiTokensSection />)}
        {tab === "mcp" && <McpSettingsSection canAdminister={isAdmin} />}
        {tab === "connections" && <section aria-labelledby="connections-heading" className="space-y-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 id="connections-heading" ref={connectionHeading} tabIndex={-1} className="text-xl font-semibold tracking-tight">AI connections</h2>
              <p className="mt-1 text-sm text-muted-foreground">Connections and default models for manual and automatic reviews.</p>
            </div>
            {isAdmin && <Button disabled={!providers.data} onClick={(event) => {
              returnFocus.current = event.currentTarget;
              setEditing("new");
            }}><Plus className="size-4" />Add connection</Button>}
          </div>
          <ErrorNotice error={connections.error || providers.error || (!deleting && action.error)} />
          {providers.isError && <Button variant="outline" size="sm" disabled={providers.isFetching} onClick={() => { void providers.refetch(); }}>
            Retry providers
          </Button>}
          {connections.isPending && <Loading />}
          {connections.data && <div className="space-y-6">
            {groups.map((group) => <section key={group.title} aria-label={group.title}>
              <h3 className="mb-2.5 ml-0.5 flex items-baseline gap-2 text-sm font-semibold">
                {group.title}<span className="text-xs font-normal tabular-nums text-muted-foreground">{group.items.length}</span>
              </h3>
              {!group.items.length ? <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-dashed p-5">
                <p className="max-w-md text-sm leading-relaxed text-muted-foreground">{isAdmin ? "Add an API connection for reviews shared with your team." : "An administrator can add an API connection for your team."}</p>
                {isAdmin && <Button variant="outline" size="sm" disabled={!providers.data} onClick={(event) => {
                  returnFocus.current = event.currentTarget;
                  setEditing("new");
                }}>Add API connection</Button>}
              </div> : <div className="overflow-hidden rounded-lg border bg-card">
                <div aria-hidden="true" className="settings-record settings-connection-row settings-list-heading border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground">
                  <span>Connection</span><span>Default model</span><span>Account status</span><span />
                </div>
                {group.items.map((connection) => {
                  const kindLabel = `${connection.provider} · ${connection.protocol}`;
                  const canManage = isAdmin;
                  const logo = apiProviderLogos.get(connection.provider);
                  const testing = connection.last_test === "Testing connection…"
                    || (action.isPending && action.variables?.path === `/connections/${connection.id}/test`);
                  const testStatus = testing ? "running" : !connection.last_test ? "not_tested"
                    : connection.last_test.startsWith("Connected.") ? "connected" : "failed";
                  const testDetail = testing ? "Testing connection…" : connection.last_test ?? undefined;
                  return <article key={connection.id} aria-label={connection.name}
                    className="settings-record settings-connection-row border-b px-4 py-4 text-sm last:border-b-0 sm:px-5">
                    <div className="settings-identity flex min-w-0 items-start gap-3">
                      {logo ? <ProviderLogo logo={logo} className="mt-0.5" /> : <Plug aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />}
                      <div className="min-w-0">
                        <h4 className="break-words font-medium">{connection.name}</h4>
                        <p className="mt-1 break-words text-xs text-muted-foreground">
                          {kindLabel}
                        </p>
                      </div>
                    </div>
                    <div className="min-w-0">
                      <span aria-hidden="true" className="settings-cell-label mb-1 text-xs text-muted-foreground">Default model</span>
                      <p className="break-words text-xs leading-relaxed"><span className="sr-only">Default model: </span>{connection.model || "Choose a model"}</p>
                    </div>
                    <div className="min-w-0 text-xs text-muted-foreground" role="status"
                      title={testDetail}
                      aria-description={testDetail}>
                      <span className="settings-cell-label mb-1">Account status</span>
                      {<Status value={testStatus}
                        label={testing ? "Testing" : testStatus === "not_tested" ? "Not tested" : undefined}
                        icon={testStatus === "connected" ? <Check aria-hidden="true" className="size-3" /> : undefined} />}
                    </div>
                    <div className="settings-actions flex items-center justify-end gap-1">
                      {canManage && <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="size-8" disabled={action.isPending} aria-label={`Actions for ${connection.name}`}
                              onPointerDown={(event) => { returnFocus.current = event.currentTarget; }}
                              onFocus={(event) => { returnFocus.current = event.currentTarget; }}>
                              <MoreHorizontal className="size-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" onCloseAutoFocus={(event) => { if (editing || deleting) event.preventDefault(); }}>
                            <DropdownMenuItem disabled={!providers.data} onSelect={() => setEditing(connection)}>Edit</DropdownMenuItem>
                            <DropdownMenuItem disabled={testing} onSelect={() => {
                              action.reset(); action.mutate({ path: `/connections/${connection.id}/test` });
                            }}>{testing ? "Testing…" : testStatus === "connected" ? "Test again" : "Test connection"}</DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem variant="destructive" onSelect={() => { action.reset(); setDeleting(connection); }}>Delete</DropdownMenuItem>
                          </DropdownMenuContent>
                      </DropdownMenu>}
                    </div>
                  </article>;
                })}
              </div>}
            </section>)}
          </div>}
          <p className="text-xs leading-relaxed text-muted-foreground">
            Shared API connections are managed by administrators. Review and security history remains available after a connection is retired.
          </p>
        </section>}
        {(tab === "github" || tab === "team") && query.isPending && <Loading />}
        {tab === "github" && instance && <section aria-label="GitHub" className="space-y-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-xl font-semibold tracking-tight">GitHub</h2>
              <p className="mt-1 text-sm text-muted-foreground">Manage the accounts and repositories connected to your review app.</p>
            </div>
            <Button asChild><a href={instance.github_app.install_url} target="_blank" rel="noreferrer">
              Install on GitHub<ArrowUpRight className="size-4" />
            </a></Button>
          </div>
          <section aria-label="GitHub App">
            <h3 className="mb-2.5 text-sm font-semibold">GitHub App</h3>
            <dl className="divide-y rounded-lg border bg-card text-sm">
              <div className="grid gap-2 px-5 py-4 sm:grid-cols-[128px_minmax(0,1fr)]">
                <dt className="text-xs text-muted-foreground">App identity</dt>
                <dd className="min-w-0">
                  <p className="flex items-center gap-2 font-medium">
                    <Github aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" /><span className="min-w-0 break-words">{instance.github_app.slug}</span>
                    {instance.github_app.slug && <Button asChild variant="ghost" size="icon" className="-my-1 size-8 shrink-0 text-muted-foreground">
                      <a href={`https://github.com/apps/${encodeURIComponent(instance.github_app.slug)}`} target="_blank" rel="noreferrer"
                        aria-label="Open GitHub App on GitHub" title="Open GitHub App on GitHub"><ArrowUpRight aria-hidden="true" className="size-3.5" /></a>
                    </Button>}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">Comments are published as this GitHub App.</p>
                </dd>
              </div>
              <div className="grid gap-2 px-5 py-4 sm:grid-cols-[128px_minmax(0,1fr)]">
                <dt className="text-xs text-muted-foreground">Webhook endpoint</dt>
                <dd className="min-w-0">
                  <div className="flex items-start gap-2">
                    <span className="min-w-0 break-all font-mono text-xs leading-relaxed">{webhookEndpoint}</span>
                    <Button variant="ghost" size="icon" className="-my-1.5 size-8 shrink-0 text-muted-foreground" aria-label="Copy webhook endpoint"
                      title={webhookCopy === "copied" ? "Copied" : "Copy webhook endpoint"} onClick={async () => {
                        setWebhookCopy(null);
                        try {
                          await navigator.clipboard.writeText(webhookEndpoint);
                          setWebhookCopy("copied");
                        } catch { setWebhookCopy("failed"); }
                      }}>
                      {webhookCopy === "copied" ? <Check aria-hidden="true" className="size-3.5" /> : <Copy aria-hidden="true" className="size-3.5" />}
                    </Button>
                  </div>
                  {webhookCopy === "copied" && <span role="status" className="sr-only">Webhook endpoint copied.</span>}
                  {webhookCopy === "failed" && <p role="alert" className="mt-2 text-xs text-destructive">Could not copy. Select the endpoint and copy it manually.</p>}
                </dd>
              </div>
            </dl>
          </section>
          <GitHubInstallations />
        </section>}
        {tab === "team" && instance && <section aria-labelledby="members-heading" className="space-y-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 id="members-heading" ref={memberHeading} tabIndex={-1} className="text-xl font-semibold tracking-tight">Team</h2>
              <p className="mt-1 text-sm text-muted-foreground">Manage who can sign in to this instance.</p>
            </div>
            <Button disabled={memberAction.isPending} onClick={(event) => { returnFocus.current = event.currentTarget; openMember("new"); }}>
              <Plus className="size-4" />Add member
            </Button>
          </div>
          {!member && !removingMember && <ErrorNotice error={memberAction.error} />}
          <div className="overflow-hidden rounded-lg border bg-card">
            <div aria-hidden="true" className="settings-member-row settings-member-heading border-b bg-muted/30 px-4 py-3 text-xs font-medium text-muted-foreground sm:px-5">
              <span>Member</span><span>Role</span><span />
            </div>
            {instance.members.map((item) => <div key={item.id} className="settings-member-row border-b px-4 py-4 last:border-b-0 sm:px-5">
              <div className="settings-member-identity flex min-w-0 items-center">
                <UserAvatar user={item.id === user.id ? user : item} className="settings-member-avatar size-(--settings-avatar-size)" />
                <div className="min-w-0">
                  <p className="break-words text-sm font-medium">@{item.login}</p>
                  {item.id === user.id && <p className="mt-0.5 text-xs text-muted-foreground">You</p>}
                </div>
              </div>
              <div className="settings-member-role"><Badge variant="outline" className="font-normal">{memberRoleLabel(item.role)}</Badge></div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="settings-member-actions size-8" disabled={memberAction.isPending} aria-label={`Actions for @${item.login}`}
                    onPointerDown={(event) => { returnFocus.current = event.currentTarget; }}
                    onFocus={(event) => { returnFocus.current = event.currentTarget; }}><MoreHorizontal className="size-4" /></Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" onCloseAutoFocus={(event) => { if (member || removingMember) event.preventDefault(); }}>
                  <DropdownMenuItem disabled={item.id === user.id || item.id === lastAdministrator}
                    onSelect={() => openMember(item)}>Edit role</DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive" disabled={item.id === user.id || item.id === lastAdministrator} onSelect={() => {
                    memberAction.reset();
                    setRemovingMember(item);
                  }}>Remove</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>)}
          </div>
          <p className="flex items-start gap-2 text-xs leading-relaxed text-muted-foreground">
            <Github aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
            Each person signs in with GitHub and only sees repositories they can access there.
          </p>
        </section>}
        {tab !== "appearance" && !!instance?.failed_jobs.length && <details className="pt-5">
          <summary className="cursor-pointer text-sm text-muted-foreground">Recent background errors ({instance.failed_jobs.length})</summary>
          <ul className="mt-4 space-y-3">
            {instance.failed_jobs.map((job) => <li key={job.id} className="rounded-md border p-3 text-xs">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <span className="font-semibold">{job.kind}</span>
                {job.available_at && <time dateTime={job.available_at} className="text-muted-foreground">Scheduled {date(job.available_at)}</time>}
              </div>
              <div className="mt-2"><ErrorNotice error={job.error} /></div>
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-muted-foreground">
                {job.attempts !== undefined && <span>{job.attempts} {job.attempts === 1 ? "attempt" : "attempts"}</span>}
                <span className="min-w-0 break-all">Job <code>{job.id}</code></span>
              </div>
            </li>)}
          </ul>
        </details>}
      </div>
    </div>
    <Dialog open={!!editing} onOpenChange={(open) => {
      if (!open && !savingConnection) setEditing(null);
    }}>
      <DialogContent className={editing && editing !== "new" ? "flex h-[min(660px,90dvh)] flex-col overflow-hidden sm:max-w-lg"
        : "max-h-[90dvh] overflow-y-auto sm:max-w-xl"} onCloseAutoFocus={restoreFocus}>
        {editing && <DialogHeader className="shrink-0">
          <DialogTitle>{editing === "new" ? "Add AI connection" : "Edit AI connection"}</DialogTitle>
          <DialogDescription>Credentials are encrypted on this instance.</DialogDescription>
        </DialogHeader>}
        {editing && providers.data && <ConnectionForm key={editing === "new" ? "new" : editing.id}
          connection={editing === "new" ? undefined : editing} providers={providers.data}
          onSavingChange={setSavingConnection}
          onSaved={() => setEditing(null)} />}
      </DialogContent>
    </Dialog>
    <Dialog open={!!deleting} onOpenChange={(open) => { if (!open && !action.isPending) { setDeleting(null); action.reset(); } }}>
      <DialogContent onCloseAutoFocus={restoreFocus}>
        <DialogHeader>
          <DialogTitle>Delete {deleting?.name}?</DialogTitle>
          <DialogDescription>Repositories must stop using this connection before it can be deleted.</DialogDescription>
        </DialogHeader>
        <ErrorNotice error={action.error} />
        <div className="flex justify-end gap-2">
          <Button variant="outline" disabled={action.isPending} onClick={() => { setDeleting(null); action.reset(); }}>Cancel</Button>
          <Button variant="destructive" disabled={action.isPending} onClick={() => {
            if (deleting) action.mutate({ path: `/connections/${deleting.id}`, method: "DELETE" }, {
              onSuccess: () => { returnFocus.current = connectionHeading.current; setDeleting(null); },
            });
          }}>{action.isPending ? "Deleting…" : "Delete connection"}</Button>
        </div>
      </DialogContent>
    </Dialog>
    <Dialog open={!!removingMember} onOpenChange={(open) => { if (!open && !memberAction.isPending) { setRemovingMember(null); memberAction.reset(); } }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto" onCloseAutoFocus={restoreFocus}>
        <DialogHeader>
          <DialogTitle>Remove @{removingMember?.login}?</DialogTitle>
          <DialogDescription>
            This revokes their access, ends their sessions, and cancels their queued, running, or blocked reviews.
            To restore access, add them again.
          </DialogDescription>
        </DialogHeader>
        <ErrorNotice error={memberAction.error} />
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="outline" disabled={memberAction.isPending} onClick={() => { setRemovingMember(null); memberAction.reset(); }}>Cancel</Button>
          <Button variant="destructive" disabled={memberAction.isPending} onClick={() => {
            if (removingMember) memberAction.mutate({ path: `/members/${removingMember.id}`, method: "DELETE" }, {
              onSuccess: () => { returnFocus.current = memberHeading.current; setRemovingMember(null); },
            });
          }}>{memberAction.isPending ? "Removing…" : "Remove member"}</Button>
        </div>
      </DialogContent>
    </Dialog>
    <Dialog open={!!member} onOpenChange={(open) => { if (!open && !memberAction.isPending) { setMember(null); memberAction.reset(); } }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto" onCloseAutoFocus={restoreFocus}>
        <DialogHeader>
          <DialogTitle>{member === "new" ? "Add member" : "Edit role"}</DialogTitle>
          <DialogDescription>{member === "new" ? "Allow a GitHub account to sign in. Entering an existing member updates their role." : "Update this member’s access to instance settings."}</DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={async (event) => {
          event.preventDefault();
          if (memberAction.isPending) return;
          try {
            await memberAction.mutateAsync({ path: "/members", body: { login, role: memberRoleLocked ? "admin" : role } });
            setMember(null);
          } catch { /* Keep the draft and display the action error. */ }
        }}>
          <ErrorNotice error={memberAction.error} />
          <Field label="GitHub username">{(id) => <Input id={id} required readOnly={member !== "new"} value={login}
            onChange={(event) => setLogin(event.target.value)} placeholder="username" />}</Field>
          <Field label="Role" hint={memberRoleLocked ? "Administrator access must be retained. Add another administrator before removing the last one." : undefined}>{(id) => <SearchSelect id={id} value={memberRoleLocked ? "admin" : role} disabled={memberRoleLocked} onChange={setRole}
            options={[{ value: "member", label: "Member" }, { value: "admin", label: "Administrator" }]} />}</Field>
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="outline" disabled={memberAction.isPending} onClick={() => { setMember(null); memberAction.reset(); }}>Cancel</Button>
            <Button type="submit" disabled={memberAction.isPending}>{memberAction.isPending ? "Saving…" : memberSubmitLabel}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  </>;
}
