import { useId, type CSSProperties } from "react";
import { Check, GitPullRequest } from "lucide-react";
import { themes, type ColorMode, type ThemeId } from "../lib/appearance";
import { Field } from "./common";
import { Label } from "./ui/label";

export function AppearanceSettings({ mode, theme, dark, onModeChange, onThemeChange }: {
  mode: ColorMode;
  theme: ThemeId;
  dark: boolean;
  onModeChange: (mode: ColorMode) => void;
  onThemeChange: (theme: ThemeId) => void;
}) {
  const id = useId();
  return <section aria-labelledby="appearance-heading" className="space-y-6">
    <div>
      <h2 id="appearance-heading" className="text-xl font-semibold tracking-tight">Appearance</h2>
      <p className="mt-1 text-sm text-muted-foreground">Choose how Code Mortis looks. Changes apply immediately and are saved in this browser.</p>
    </div>
    <div className="max-w-xs">
      <Field label="Color mode" hint="System follows your device’s light or dark appearance.">
        {(fieldId) => <select id={fieldId} value={mode} onChange={(event) => onModeChange(event.target.value as ColorMode)}
          className="h-9 w-full rounded-md border border-input bg-card px-3 text-base shadow-xs md:text-sm">
          <option value="light">Light</option>
          <option value="dark">Dark</option>
          <option value="system">System</option>
        </select>}
      </Field>
    </div>
    <fieldset>
      <legend className="mb-3 text-sm font-medium">Theme</legend>
      <div className="grid grid-cols-1 gap-3 @min-[440px]/settings:grid-cols-2 @min-[720px]/settings:grid-cols-3">
        {themes.map((item) => <div key={item.id} className="min-w-0">
          <input id={`${id}-${item.id}`} name={`${id}-theme`} type="radio" value={item.id}
            checked={theme === item.id} onChange={() => onThemeChange(item.id)} className="peer sr-only" />
          <Label htmlFor={`${id}-${item.id}`} className="block cursor-pointer overflow-hidden rounded-lg border p-1 transition-colors hover:border-primary/60 peer-checked:border-primary peer-checked:ring-1 peer-checked:ring-primary peer-focus-visible:outline-2 peer-focus-visible:outline-offset-4 peer-focus-visible:outline-ring">
            <div aria-hidden="true" style={item[dark ? "dark" : "light"] as CSSProperties}
              className="flex h-28 overflow-hidden rounded-md border bg-background text-foreground">
              <div className="flex w-1/4 flex-col gap-2 border-r bg-muted p-3">
                <div className="mb-1 h-2 w-2/3 rounded-sm bg-primary" />
                <div className="h-2 rounded-sm bg-accent" />
                <div className="h-1.5 w-3/4 rounded-sm bg-muted-foreground/40" />
                <div className="h-1.5 w-2/3 rounded-sm bg-muted-foreground/40" />
              </div>
              <div className="min-w-0 flex-1 p-3">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <span className="text-[10px] font-semibold">Pull requests</span>
                  <span className="rounded-sm bg-primary px-1.5 py-1 text-[8px] text-primary-foreground">Review</span>
                </div>
                <div className="flex items-center gap-2 rounded border bg-card p-2">
                  <GitPullRequest className="size-3 shrink-0 text-primary" />
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <div className="h-1.5 w-3/4 rounded-sm bg-foreground/60" />
                    <div className="h-1 w-1/2 rounded-sm bg-muted-foreground/40" />
                  </div>
                </div>
              </div>
            </div>
            <span className="flex min-h-10 items-center justify-between gap-2 px-2 py-2 text-sm">
              <span>{item.label}</span>
              {theme === item.id && <span className="flex items-center gap-1 text-xs text-primary"><Check aria-hidden="true" className="size-3.5" /><span aria-hidden="true">Selected</span></span>}
            </span>
          </Label>
        </div>)}
      </div>
    </fieldset>
  </section>;
}
